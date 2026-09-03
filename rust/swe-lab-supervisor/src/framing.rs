//! Line framing for the actor's stdout: a growable buffer with a ceiling.
//!
//! One `stream-json` event is one line, and a tool result can make a line
//! large, so the buffer grows to the configured ceiling rather than being
//! fixed. A line that passes the ceiling is not held: its bytes are handed on
//! in pieces so the event log can still receive them verbatim, and the line
//! reaches no judgment. Every frame says whether the actor ended it with a
//! newline, so the log can reproduce the stream byte for byte — a last line
//! the actor left unterminated is logged unterminated, not amended.

/// What the framer hands on.
#[derive(Debug, PartialEq, Eq)]
pub enum Frame {
    /// One complete line within the ceiling.
    Line {
        /// The bytes, newline excluded.
        bytes: Vec<u8>,
        /// Whether the actor ended the line with `\n`. False only for the
        /// last line of the stream, left unterminated at end of input.
        newline: bool,
    },
    /// A piece of a line that passed the ceiling.
    Oversized {
        /// The bytes, verbatim.
        part: Vec<u8>,
        /// Whether this piece ends the line.
        last: bool,
        /// Whether the line ended with `\n` (meaningful only when `last`;
        /// false at end of input).
        newline: bool,
    },
}

/// Splits a byte stream into newline-terminated lines, bounded in memory.
#[derive(Debug)]
pub struct Framer {
    buffer: Vec<u8>,
    ceiling: usize,
    /// Inside a line that already passed the ceiling: pieces pass through
    /// until its newline.
    oversized: bool,
}

impl Framer {
    /// A framer that holds at most `ceiling` bytes of one unfinished line.
    #[must_use]
    pub fn new(ceiling: usize) -> Self {
        Self {
            buffer: Vec::new(),
            ceiling,
            oversized: false,
        }
    }

    /// Feed one chunk of input, appending every frame it completes to `out`.
    pub fn push(&mut self, mut chunk: &[u8], out: &mut Vec<Frame>) {
        while !chunk.is_empty() {
            if let Some(at) = chunk.iter().position(|&byte| byte == b'\n') {
                out.push(self.end_line(&chunk[..at], true));
                chunk = &chunk[at + 1..];
            } else {
                if let Some(frame) = self.extend_line(chunk) {
                    out.push(frame);
                }
                chunk = &[];
            }
        }
    }

    /// End of input: hand on whatever is still buffered as a final line,
    /// unterminated — an actor that died mid-write leaves one.
    pub fn finish(&mut self, out: &mut Vec<Frame>) {
        if self.oversized || !self.buffer.is_empty() {
            out.push(self.end_line(&[], false));
        }
    }

    fn end_line(&mut self, head: &[u8], newline: bool) -> Frame {
        if self.oversized {
            self.oversized = false;
            return Frame::Oversized {
                part: head.to_vec(),
                last: true,
                newline,
            };
        }
        let mut bytes = std::mem::take(&mut self.buffer);
        bytes.extend_from_slice(head);
        if bytes.len() > self.ceiling {
            Frame::Oversized {
                part: bytes,
                last: true,
                newline,
            }
        } else {
            Frame::Line { bytes, newline }
        }
    }

    fn extend_line(&mut self, chunk: &[u8]) -> Option<Frame> {
        if self.oversized {
            return Some(Frame::Oversized {
                part: chunk.to_vec(),
                last: false,
                newline: false,
            });
        }
        if self.buffer.len() + chunk.len() > self.ceiling {
            self.oversized = true;
            let mut part = std::mem::take(&mut self.buffer);
            part.extend_from_slice(chunk);
            return Some(Frame::Oversized {
                part,
                last: false,
                newline: false,
            });
        }
        self.buffer.extend_from_slice(chunk);
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frames(ceiling: usize, chunks: &[&[u8]]) -> Vec<Frame> {
        let mut framer = Framer::new(ceiling);
        let mut out = Vec::new();
        for chunk in chunks {
            framer.push(chunk, &mut out);
        }
        framer.finish(&mut out);
        out
    }

    fn line(text: &str) -> Frame {
        Frame::Line {
            bytes: text.as_bytes().to_vec(),
            newline: true,
        }
    }

    fn unterminated(text: &str) -> Frame {
        Frame::Line {
            bytes: text.as_bytes().to_vec(),
            newline: false,
        }
    }

    #[test]
    fn lines_are_reassembled_across_chunk_boundaries() {
        assert_eq!(
            frames(64, &[b"ab", b"c\nde", b"\n\nf"]),
            vec![line("abc"), line("de"), line(""), unterminated("f")]
        );
    }

    #[test]
    fn a_line_over_the_ceiling_passes_through_in_pieces_and_framing_resumes_after_it() {
        let out = frames(4, &[b"ok\n", b"toolong", b"er\nfine\n"]);
        assert_eq!(
            out,
            vec![
                line("ok"),
                Frame::Oversized {
                    part: b"toolong".to_vec(),
                    last: false,
                    newline: false,
                },
                Frame::Oversized {
                    part: b"er".to_vec(),
                    last: true,
                    newline: true,
                },
                line("fine"),
            ]
        );
        // Reassembling the oversized pieces gives back the line verbatim, which
        // is what lets the event log stay complete.
        let rebuilt: Vec<u8> = out
            .iter()
            .filter_map(|f| match f {
                Frame::Oversized { part, .. } => Some(part.clone()),
                Frame::Line { .. } => None,
            })
            .flatten()
            .collect();
        assert_eq!(rebuilt, b"toolonger");
    }

    #[test]
    fn a_line_exactly_at_the_ceiling_is_a_line_and_one_byte_more_is_not() {
        assert_eq!(frames(3, &[b"abc\n"]), vec![line("abc")]);
        assert_eq!(
            frames(3, &[b"abcd\n"]),
            vec![Frame::Oversized {
                part: b"abcd".to_vec(),
                last: true,
                newline: true,
            }]
        );
    }

    #[test]
    fn the_buffer_never_holds_more_than_the_ceiling() {
        let mut framer = Framer::new(8);
        let mut out = Vec::new();
        framer.push(&[b'x'; 1000], &mut out);
        assert!(framer.buffer.len() <= 8);
        assert!(framer.oversized);
        framer.push(b"\nafter", &mut out);
        assert_eq!(framer.buffer, b"after");
    }

    #[test]
    fn an_unterminated_tail_is_a_line_that_says_it_had_no_newline() {
        assert_eq!(frames(64, &[b"a\nb"]), vec![line("a"), unterminated("b")]);
        assert_eq!(frames(64, &[b"a\n"]), vec![line("a")]);
        let mut framer = Framer::new(2);
        let mut out = Vec::new();
        framer.push(b"long", &mut out);
        framer.finish(&mut out);
        assert_eq!(
            out.last(),
            Some(&Frame::Oversized {
                part: Vec::new(),
                last: true,
                newline: false,
            })
        );
    }
}
