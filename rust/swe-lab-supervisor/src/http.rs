//! One HTTP/1.1 `POST` with a JSON body, on a plain TCP stream.
//!
//! A framework is not needed for one request shape, and every crate that
//! would provide it drags in more than this binary is allowed to carry. The
//! endpoint is loopback by design (a forwarder terminates TLS), so this
//! speaks `http://` only, closes the connection after one exchange, and reads
//! the response under one deadline for the whole call — connect included —
//! rather than a per-read timeout a slow drip could outlast.

use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::time::{Duration, Instant};

use crate::config::Endpoint;
use crate::signals::{self, Stop};

/// How long one wait on the socket lasts before the stop flag is looked
/// at again: a call in progress returns as cancelled within this of the
/// wrapper being told to stop, however far its deadline is.
const CANCEL_POLL: Duration = Duration::from_millis(100);

/// The most a response may be; a model response is kilobytes, and a
/// forwarder gone wrong should not fill memory.
const MAX_RESPONSE_BYTES: usize = 16 * 1024 * 1024;

/// A response, decoded as far as the status line and the body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    /// The status code.
    pub status: u16,
    /// The body, de-chunked when the server chunked it.
    pub body: Vec<u8>,
}

/// `POST` `body` as Anthropic Messages JSON to the endpoint, optionally with
/// an API key, and
/// return the response before `deadline` — or as cancelled, once `stop` is
/// raised.
///
/// # Errors
///
/// The wrapper was told to stop; the connection or the exchange does not
/// complete before the deadline; the response is not HTTP/1.x; or the body
/// exceeds
/// [`MAX_RESPONSE_BYTES`]. The message never contains the request body,
/// the token, or any byte of the response: what a peer sent is not
/// quoted back, a malformed status line or chunk size included — the
/// response is where a reflected credential would be.
pub fn post_json(
    endpoint: &Endpoint,
    api_key: Option<&str>,
    body: &[u8],
    deadline: Instant,
    stop: &Stop,
) -> Result<Response, String> {
    // Nothing to resolve: the endpoint is a numeric loopback address by
    // construction (`Endpoint::parse`), so the connection is the first
    // thing that can wait, and it waits like every other: a slice at a
    // time, the stop looked at between slices. An attempt a slice did not
    // complete is abandoned and made again — a listener whose queue is
    // full drops the attempt anyway, and the next one is what finds room.
    let mut stream = loop {
        match TcpStream::connect_timeout(&endpoint.address, slice(deadline, stop)?) {
            Ok(stream) => break stream,
            Err(e)
                if e.kind() == io::ErrorKind::TimedOut || e.kind() == io::ErrorKind::WouldBlock => {
            }
            Err(e) => return Err(format!("connecting to the endpoint: {e}")),
        }
    };
    let mut head = format!(
        "POST {} HTTP/1.1\r\nHost: {}\r\nContent-Type: application/json\r\nAccept: application/json\r\nContent-Length: {}\r\nConnection: close\r\n",
        endpoint.path,
        endpoint.address,
        body.len()
    );
    head.push_str("anthropic-version: 2023-06-01\r\n");
    if let Some(key) = api_key {
        head.push_str("x-api-key: ");
        head.push_str(key);
        head.push_str("\r\n");
    }
    head.push_str("\r\n");
    // One absolute deadline for the whole call: each wait on the socket is
    // a slice of what is left of it, looked at again between slices along
    // with the stop flag — per write, not per `write_all`, since a peer
    // that takes a little before each timeout would otherwise stretch a
    // large body past the deadline one partial write at a time.
    for part in [head.as_bytes(), body] {
        let mut sent = 0;
        while sent < part.len() {
            stream
                .set_write_timeout(Some(slice(deadline, stop)?))
                .map_err(|e| e.to_string())?;
            match stream.write(&part[sent..]) {
                Ok(0) => return Err("sending the request: the connection took nothing".to_string()),
                Ok(n) => sent += n,
                Err(e)
                    if e.kind() == io::ErrorKind::Interrupted
                        || e.kind() == io::ErrorKind::WouldBlock
                        || e.kind() == io::ErrorKind::TimedOut => {}
                Err(e) => return Err(format!("sending the request: {e}")),
            }
        }
    }
    let raw = read_until_close(&mut stream, deadline, stop)?;
    parse(&raw)
}

fn remaining(deadline: Instant) -> Result<Duration, String> {
    let left = deadline.saturating_duration_since(Instant::now());
    if left.is_zero() {
        return Err("the call's deadline passed".to_string());
    }
    Ok(left)
}

/// The next wait on the socket: none once the wrapper was told to stop or
/// the deadline has passed, and at most [`CANCEL_POLL`] otherwise.
fn slice(deadline: Instant, stop: &Stop) -> Result<Duration, String> {
    if signals::requested(stop).is_some() {
        return Err("the run was cancelled".to_string());
    }
    Ok(remaining(deadline)?.min(CANCEL_POLL))
}

fn read_until_close(
    stream: &mut TcpStream,
    deadline: Instant,
    stop: &Stop,
) -> Result<Vec<u8>, String> {
    let mut raw = Vec::new();
    let mut chunk = [0u8; 16 * 1024];
    loop {
        stream
            .set_read_timeout(Some(slice(deadline, stop)?))
            .map_err(|e| e.to_string())?;
        match stream.read(&mut chunk) {
            Ok(0) => return Ok(raw),
            Ok(n) => {
                raw.extend_from_slice(&chunk[..n]);
                if raw.len() > MAX_RESPONSE_BYTES {
                    return Err(format!("the response exceeds {MAX_RESPONSE_BYTES} bytes"));
                }
            }
            Err(e)
                if e.kind() == io::ErrorKind::Interrupted
                    || e.kind() == io::ErrorKind::WouldBlock
                    || e.kind() == io::ErrorKind::TimedOut => {}
            Err(e) => return Err(format!("reading the response: {e}")),
        }
    }
}

fn parse(raw: &[u8]) -> Result<Response, String> {
    let split = find(raw, b"\r\n\r\n").ok_or("the response has no header block")?;
    let head = std::str::from_utf8(&raw[..split]).map_err(|_| "the response head is not UTF-8")?;
    let body = &raw[split + 4..];
    let mut lines = head.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let status = status_line
        .strip_prefix("HTTP/1.")
        .and_then(|rest| rest.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .ok_or("the response does not start with an HTTP/1.x status line")?;
    let mut chunked = false;
    let mut content_length = None;
    for line in lines {
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        let value = value.trim();
        if name.eq_ignore_ascii_case("transfer-encoding") {
            chunked = value.eq_ignore_ascii_case("chunked");
        } else if name.eq_ignore_ascii_case("content-length") {
            content_length = value.parse::<usize>().ok();
        }
    }
    let body = if chunked {
        dechunk(body)?
    } else if let Some(length) = content_length {
        body.get(..length)
            .ok_or_else(|| format!("the body is shorter than its Content-Length {length}"))?
            .to_vec()
    } else {
        body.to_vec()
    };
    Ok(Response { status, body })
}

fn dechunk(mut body: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    loop {
        let line_end = find(body, b"\r\n").ok_or("a chunk has no size line")?;
        let size_text =
            std::str::from_utf8(&body[..line_end]).map_err(|_| "a chunk size is not text")?;
        let size_text = size_text.split(';').next().unwrap_or_default().trim();
        let size = usize::from_str_radix(size_text, 16).map_err(|_| "a chunk size is not hex")?;
        body = &body[line_end + 2..];
        if size == 0 {
            return Ok(out);
        }
        let data = body.get(..size).ok_or("a chunk is shorter than its size")?;
        out.extend_from_slice(data);
        body = body.get(size + 2..).ok_or("a chunk has no terminator")?;
    }
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
pub(crate) mod tests {
    use std::net::TcpListener;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::mpsc;
    use std::thread;

    use super::*;

    /// A stop never raised.
    static NEVER: Stop = AtomicUsize::new(0);

    /// The call under test, with a stop that is never raised.
    fn post_json(
        endpoint: &Endpoint,
        api_key: Option<&str>,
        body: &[u8],
        deadline: Instant,
    ) -> Result<Response, String> {
        super::post_json(endpoint, api_key, body, deadline, &NEVER)
    }

    /// A connect that cannot complete — the listener's accept queue is
    /// full, so the kernel drops the attempt and the client would wait,
    /// retransmitting, for as long as it is given — returns as cancelled
    /// within a poll interval of the stop, however far the deadline is;
    /// the control is the same connect with no stop, which waits for its
    /// deadline.
    #[test]
    fn a_cancellation_reaches_a_connect_that_cannot_complete() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let mut fillers = Vec::new();
        loop {
            match TcpStream::connect_timeout(&address, Duration::from_millis(300)) {
                Ok(stream) => fillers.push(stream),
                Err(e) if e.kind() == io::ErrorKind::TimedOut => break,
                Err(e) => panic!("filling the accept queue: {e}"),
            }
            assert!(fillers.len() < 4096, "the accept queue never filled");
        }
        let endpoint = Endpoint {
            address,
            path: "/".to_string(),
        };
        let stop = std::sync::Arc::new(AtomicUsize::new(0));
        let raised = std::sync::Arc::clone(&stop);
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            raised.store(15, Ordering::Relaxed);
        });
        let started = Instant::now();
        let error = super::post_json(
            &endpoint,
            None,
            b"{}",
            started + Duration::from_secs(10),
            &stop,
        )
        .unwrap_err();
        assert!(error.contains("cancelled"), "{error}");
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "{:?}",
            started.elapsed()
        );

        let started = Instant::now();
        let error =
            post_json(&endpoint, None, b"{}", started + Duration::from_millis(400)).unwrap_err();
        assert!(error.contains("deadline"), "{error}");
        assert!(
            started.elapsed() >= Duration::from_millis(400),
            "{:?}",
            started.elapsed()
        );
        drop(fillers);
        drop(listener);
    }

    /// A call in progress returns as cancelled within a poll interval of
    /// the stop being raised, however far its deadline is; the control is
    /// the same call with the stop never raised, which waits for the
    /// deadline.
    #[test]
    fn a_call_in_progress_returns_as_cancelled_when_the_stop_is_raised() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = Endpoint {
            address: std::net::SocketAddr::from((
                [127, 0, 0, 1],
                listener.local_addr().unwrap().port(),
            )),
            path: "/".to_string(),
        };
        let stop = std::sync::Arc::new(AtomicUsize::new(0));
        let raised = std::sync::Arc::clone(&stop);
        thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            raised.store(15, Ordering::Relaxed);
        });
        let started = Instant::now();
        let error = super::post_json(
            &endpoint,
            None,
            b"{}",
            started + Duration::from_secs(10),
            &stop,
        )
        .unwrap_err();
        assert!(error.contains("cancelled"), "{error}");
        assert!(
            started.elapsed() < Duration::from_secs(2),
            "{:?}",
            started.elapsed()
        );

        let started = Instant::now();
        let error =
            post_json(&endpoint, None, b"{}", started + Duration::from_millis(300)).unwrap_err();
        assert!(error.contains("deadline"), "{error}");
        drop(listener);
    }

    /// Read one whole request — head and `Content-Length` body — off a
    /// socket. A stub that answers before it has read everything makes the
    /// kernel reset the connection on close, and the client sees a reset
    /// instead of the reply.
    pub(crate) fn read_request(socket: &mut TcpStream) -> Vec<u8> {
        let mut request = Vec::new();
        let mut buffer = [0u8; 4096];
        loop {
            let n = socket.read(&mut buffer).unwrap();
            assert!(n > 0, "the client closed before finishing its request");
            request.extend_from_slice(&buffer[..n]);
            if let Some(split) = find(&request, b"\r\n\r\n") {
                let head = String::from_utf8_lossy(&request[..split]).to_string();
                let length: usize = head
                    .lines()
                    .find_map(|l| l.strip_prefix("Content-Length: "))
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(0);
                if request.len() >= split + 4 + length {
                    return request;
                }
            }
        }
    }

    /// A one-shot HTTP server on loopback: it answers the first request with
    /// `reply` verbatim and hands the raw request back to the test.
    pub(crate) fn serve_once(reply: &'static str) -> (Endpoint, mpsc::Receiver<Vec<u8>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, rx) = mpsc::channel();
        let _server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let request = read_request(&mut socket);
            socket.write_all(reply.as_bytes()).unwrap();
            tx.send(request).unwrap();
        });
        (
            Endpoint {
                address: std::net::SocketAddr::from(([127, 0, 0, 1], port)),
                path: "/v1/messages".to_string(),
            },
            rx,
        )
    }

    fn soon() -> Instant {
        Instant::now() + Duration::from_secs(5)
    }

    #[test]
    fn a_post_carries_the_anthropic_headers_and_reads_a_content_length_body() {
        let (endpoint, requests) = serve_once(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"ok\":true}\r\n",
        );
        let response = post_json(&endpoint, Some("tok-123"), b"{\"q\":1}", soon()).unwrap();
        assert_eq!(response.status, 200);
        assert_eq!(response.body, b"{\"ok\":true}\r\n");
        let request = String::from_utf8(requests.recv().unwrap()).unwrap();
        assert!(request.starts_with("POST /v1/messages HTTP/1.1\r\n"));
        assert!(request.contains("\r\nx-api-key: tok-123\r\n"));
        assert!(request.contains("\r\nanthropic-version: 2023-06-01\r\n"));
        assert!(request.contains("\r\nContent-Length: 7\r\n"));
        assert!(request.ends_with("\r\n\r\n{\"q\":1}"));
    }

    #[test]
    fn without_an_api_key_no_key_header_is_sent() {
        let (endpoint, requests) = serve_once("HTTP/1.1 204 No Content\r\n\r\n");
        let response = post_json(&endpoint, None, b"{}", soon()).unwrap();
        assert_eq!(response.status, 204);
        assert!(response.body.is_empty());
        let request = String::from_utf8(requests.recv().unwrap()).unwrap();
        assert!(!request.to_ascii_lowercase().contains("x-api-key"));
        assert!(request.contains("\r\nanthropic-version: 2023-06-01\r\n"));
    }

    #[test]
    fn a_chunked_body_is_reassembled() {
        let (endpoint, _requests) = serve_once(
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6;ext=1\r\n world\r\n0\r\n\r\n",
        );
        let response = post_json(&endpoint, None, b"{}", soon()).unwrap();
        assert_eq!(response.body, b"hello world");
    }

    #[test]
    fn a_server_that_never_answers_fails_at_the_deadline_not_later() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = Endpoint {
            address: std::net::SocketAddr::from((
                [127, 0, 0, 1],
                listener.local_addr().unwrap().port(),
            )),
            path: "/".to_string(),
        };
        let started = Instant::now();
        let error =
            post_json(&endpoint, None, b"{}", started + Duration::from_millis(300)).unwrap_err();
        assert!(error.contains("deadline"), "{error}");
        assert!(started.elapsed() < Duration::from_secs(3));
        drop(listener);
    }

    #[test]
    fn a_non_http_answer_is_an_error_with_no_body_in_it() {
        let (endpoint, _requests) = serve_once("nonsense\r\n\r\n");
        let error = post_json(&endpoint, None, b"{\"secret\":1}", soon()).unwrap_err();
        assert!(error.contains("status line"), "{error}");
        assert!(!error.contains("secret"));
    }

    /// A peer that puts the API key where the parser fails — the status
    /// line, a chunk's size line — gets an error that quotes none of it:
    /// not the token, not a fragment of it.
    #[test]
    fn a_malformed_response_is_an_error_that_quotes_no_byte_of_it() {
        for reply in [
            "HTTP/1.1 tok-123-reflected OK\r\n\r\n",
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\ntok-123-reflected\r\nbody\r\n0\r\n\r\n",
        ] {
            let (endpoint, _requests) = serve_once(reply);
            let error = post_json(&endpoint, Some("tok-123-reflected"), b"{}", soon()).unwrap_err();
            assert!(!error.contains("tok"), "{error}");
            assert!(!error.contains("reflected"), "{error}");
        }
    }

    /// A peer that takes the request a few bytes at a time cannot stretch
    /// a large upload past the deadline: the timeout is recomputed before
    /// every write, so the call fails at the deadline and not after the
    /// body has trickled through.
    #[test]
    fn a_slow_reader_cannot_stretch_an_upload_past_the_deadline() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let endpoint = Endpoint {
            address: std::net::SocketAddr::from((
                [127, 0, 0, 1],
                listener.local_addr().unwrap().port(),
            )),
            path: "/".to_string(),
        };
        thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            let mut byte = [0u8; 1];
            while socket.read(&mut byte).is_ok_and(|n| n > 0) {
                thread::sleep(Duration::from_millis(20));
            }
        });
        // Larger than any loopback send buffer: the writes have to wait
        // on the peer.
        let body = vec![b'x'; 16 * 1024 * 1024];
        let started = Instant::now();
        let error =
            post_json(&endpoint, None, &body, started + Duration::from_millis(300)).unwrap_err();
        assert!(error.contains("deadline"), "{error}");
        assert!(
            started.elapsed() < Duration::from_secs(3),
            "{:?}",
            started.elapsed()
        );
    }
}
