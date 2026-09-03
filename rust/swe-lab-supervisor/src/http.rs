//! One HTTP/1.1 `POST` with a JSON body, on a plain TCP stream.
//!
//! A framework is not needed for one request shape, and every crate that
//! would provide it drags in more than this binary is allowed to carry. The
//! endpoint is loopback by design (a forwarder terminates TLS), so this
//! speaks `http://` only, closes the connection after one exchange, and reads
//! the response under one deadline for the whole call — connect included —
//! rather than a per-read timeout a slow drip could outlast.

use std::io::{self, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::{Duration, Instant};

use crate::config::Endpoint;

/// The most a response may be; a chat completion is kilobytes, and a
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

/// `POST` `body` as JSON to the endpoint, optionally with a bearer token, and
/// return the response before `deadline`.
///
/// # Errors
///
/// The host does not resolve, the connection or the exchange does not
/// complete before the deadline, the response is not HTTP/1.x, or the body
/// exceeds [`MAX_RESPONSE_BYTES`]. The message never contains the request
/// body or the token.
pub fn post_json(
    endpoint: &Endpoint,
    bearer: Option<&str>,
    body: &[u8],
    deadline: Instant,
) -> Result<Response, String> {
    let address = (endpoint.host.as_str(), endpoint.port)
        .to_socket_addrs()
        .map_err(|e| format!("resolving {}: {e}", endpoint.host))?
        .next()
        .ok_or_else(|| format!("{} resolves to no address", endpoint.host))?;
    let mut stream = TcpStream::connect_timeout(&address, remaining(deadline)?)
        .map_err(|e| format!("connecting to {address}: {e}"))?;
    let mut head = format!(
        "POST {} HTTP/1.1\r\nHost: {}:{}\r\nContent-Type: application/json\r\nAccept: application/json\r\nContent-Length: {}\r\nConnection: close\r\n",
        endpoint.path,
        endpoint.host,
        endpoint.port,
        body.len()
    );
    if let Some(token) = bearer {
        head.push_str("Authorization: Bearer ");
        head.push_str(token);
        head.push_str("\r\n");
    }
    head.push_str("\r\n");
    stream
        .set_write_timeout(Some(remaining(deadline)?))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(head.as_bytes())
        .and_then(|()| stream.write_all(body))
        .map_err(|e| format!("sending the request: {e}"))?;
    let raw = read_until_close(&mut stream, deadline)?;
    parse(&raw)
}

fn remaining(deadline: Instant) -> Result<Duration, String> {
    let left = deadline.saturating_duration_since(Instant::now());
    if left.is_zero() {
        return Err("the call's deadline passed".to_string());
    }
    Ok(left)
}

fn read_until_close(stream: &mut TcpStream, deadline: Instant) -> Result<Vec<u8>, String> {
    let mut raw = Vec::new();
    let mut chunk = [0u8; 16 * 1024];
    loop {
        stream
            .set_read_timeout(Some(remaining(deadline)?))
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
                if e.kind() == io::ErrorKind::WouldBlock || e.kind() == io::ErrorKind::TimedOut =>
            {
                return Err("the call's deadline passed while reading the response".to_string());
            }
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
        .ok_or_else(|| format!("not an HTTP/1.x status line: {status_line:?}"))?;
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
        let size = usize::from_str_radix(size_text, 16)
            .map_err(|_| format!("a chunk size is not hex: {size_text:?}"))?;
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
    use std::sync::mpsc;
    use std::thread;

    use super::*;

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
                host: "127.0.0.1".to_string(),
                port,
                path: "/v1/chat/completions".to_string(),
            },
            rx,
        )
    }

    fn soon() -> Instant {
        Instant::now() + Duration::from_secs(5)
    }

    #[test]
    fn a_post_carries_the_body_and_the_bearer_and_reads_a_content_length_body() {
        let (endpoint, requests) = serve_once(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 13\r\n\r\n{\"ok\":true}\r\n",
        );
        let response = post_json(&endpoint, Some("tok-123"), b"{\"q\":1}", soon()).unwrap();
        assert_eq!(response.status, 200);
        assert_eq!(response.body, b"{\"ok\":true}\r\n");
        let request = String::from_utf8(requests.recv().unwrap()).unwrap();
        assert!(request.starts_with("POST /v1/chat/completions HTTP/1.1\r\n"));
        assert!(request.contains("\r\nAuthorization: Bearer tok-123\r\n"));
        assert!(request.contains("\r\nContent-Length: 7\r\n"));
        assert!(request.ends_with("\r\n\r\n{\"q\":1}"));
    }

    #[test]
    fn without_a_bearer_no_authorization_header_is_sent() {
        let (endpoint, requests) = serve_once("HTTP/1.1 204 No Content\r\n\r\n");
        let response = post_json(&endpoint, None, b"{}", soon()).unwrap();
        assert_eq!(response.status, 204);
        assert!(response.body.is_empty());
        let request = String::from_utf8(requests.recv().unwrap()).unwrap();
        assert!(!request.to_ascii_lowercase().contains("authorization"));
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
            host: "127.0.0.1".to_string(),
            port: listener.local_addr().unwrap().port(),
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
}
