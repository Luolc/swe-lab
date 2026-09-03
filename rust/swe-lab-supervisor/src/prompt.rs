//! The two prompts: what the judge is asked, and what the writer is asked.
//!
//! Both are built from the observation and the criterion they are handed and
//! nothing else. They differ in one section, on purpose: **the judge is not
//! told what the supervisor has already said.** Measured on the first corpus
//! (#381), that section drove the judge's `off_track` rate from 6/330 to
//! 219/280 the moment the supervisor spoke — it stopped measuring the actor
//! and started confirming the supervisor. The writer needs the list, so as
//! not to repeat itself; the judge needs the actor's evidence alone.

use std::fmt::Write as _;

use crate::evidence::{Block, Message};

/// The cap on one correction. The enforceable part of "short, directional,
/// not a solution"; the rest is read by a human.
pub const MAX_INTERVENTION_CHARS: usize = 400;

/// What the judge is told to do.
pub const JUDGE_INSTRUCTIONS: &str = "\
You are watching an engineer work. Decide two things about the moment shown.

Judge only against the criterion given below. Do not use any other standard,
and do not reason about what the correct fix would be.

Answer with one JSON object and nothing else:
{\"off_track\": bool, \"self_correcting\": bool, \"reason\": \"<one short sentence>\"}

off_track: the work shown is off the criterion's path.
self_correcting: left alone, the engineer is already returning to it.
";

/// What the writer is told to do.
pub const WRITER_INSTRUCTIONS: &str = "\
Write one short line to the engineer, as someone watching over their shoulder.

Hedged and offhand, pointing at what to look at — never what to do. Do not
name a fix, a function, a file to edit, or a solution. No code, no diff.
At most 400 characters. Answer with the line and nothing else.
";

/// How much of one record's text reaches the prompt. A tool result can be a
/// whole file; its head — the command, the first lines of output, the error —
/// is what a human watching would read, and 4 000 characters (about a
/// thousand tokens) holds a traceback or a screenful of a file with room to
/// spare. The tail is replaced by a marker that says how much was cut.
pub const RECORD_RENDER_BUDGET_CHARS: usize = 4_000;

/// How much of the whole window reaches the prompt. Eight records at the
/// per-record cap would be 32 000 characters; this keeps the evidence section
/// near 6 000 tokens so the prompt stays well inside any model's context with
/// the criterion and the task beside it. The budget is spent newest-first, so
/// when it runs out it is the oldest records that are shortened, and once it
/// is gone the older rest is omitted under one line that says how many.
pub const WINDOW_RENDER_BUDGET_CHARS: usize = 24_000;

/// What is left of a record once the window budget has run out: enough to
/// see what kind of step it was.
const STARVED_RECORD_CHARS: usize = 200;

/// Everything a prompt may be built from. The field list is the information
/// barrier: no reference solution, hidden test, gold patch, oracle output or
/// guidebook can travel here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Observation<'a> {
    /// What the actor was asked to do.
    pub task: &'a str,
    /// The evidence window, oldest first.
    pub evidence: &'a [Message],
    /// What this supervisor has already said in this run, oldest first.
    pub said: &'a [String],
}

/// The judge's prompt: criterion, task, evidence. Nothing about what has
/// already been said (see the module note).
#[must_use]
pub fn judge_prompt(observation: &Observation<'_>, criterion: &str) -> String {
    format!(
        "# Criterion\n\n{criterion}\n\n# The task the engineer was given\n\n{}\n\n# What they have done, most recent last\n\n{}\n",
        observation.task,
        render_window(observation.evidence)
    )
}

/// The writer's prompt: the judge's, plus what has already been said.
#[must_use]
pub fn writer_prompt(observation: &Observation<'_>, criterion: &str) -> String {
    let said = if observation.said.is_empty() {
        "(nothing yet)".to_string()
    } else {
        observation.said.join("\n")
    };
    format!(
        "{}\n# What you have already said to them\n\n{said}\n",
        judge_prompt(observation, criterion)
    )
}

/// Render the window, oldest first, under the two budgets. Records the
/// window budget cannot reach are omitted as one line saying how many, not
/// rendered as a prefix each: the rendered size is bounded by the budget
/// plus the per-record framing of the records that did get a share of it,
/// however many records the window holds.
#[must_use]
pub fn render_window(records: &[Message]) -> String {
    let bodies: Vec<String> = records.iter().map(body).collect();
    // Spend the window budget newest-first: the most recent records are what
    // a verdict about "the moment shown" rests on.
    let mut remaining = WINDOW_RENDER_BUDGET_CHARS;
    let mut allowance: Vec<usize> = vec![0; bodies.len()];
    for (index, text) in bodies.iter().enumerate().rev() {
        if remaining == 0 {
            break;
        }
        let wanted = text.chars().count().min(RECORD_RENDER_BUDGET_CHARS);
        let granted = if remaining >= wanted {
            wanted
        } else {
            remaining.min(STARVED_RECORD_CHARS)
        };
        // A record with nothing to show still gets one character, so that
        // it is rendered as clipped rather than omitted: it is inside the
        // budget's reach.
        allowance[index] = granted.max(1).min(remaining);
        remaining -= allowance[index];
    }
    let omitted = allowance
        .iter()
        .take_while(|&&granted| granted == 0)
        .count();
    let mut lines = Vec::with_capacity(records.len() - omitted + 1);
    if omitted > 0 {
        lines.push(format!("[{omitted} older records not shown]"));
    }
    for ((record, text), granted) in records.iter().zip(&bodies).zip(allowance).skip(omitted) {
        lines.push(format!(
            "[{}] {}",
            record.role.as_str(),
            clip(text, granted)
        ));
    }
    lines.join("\n")
}

/// One record's text: prose as is, a tool call as its name and input, a tool
/// result in a tag that says what it is. Reasoning is not rendered — it is
/// not something the actor did, and it is often redacted.
fn body(record: &Message) -> String {
    let mut parts = Vec::with_capacity(record.blocks.len());
    for block in &record.blocks {
        match block {
            Block::Text(text) => parts.push(text.clone()),
            Block::ToolUse { name, input } => {
                parts.push(format!("<tool_use name=\"{name}\">{input}</tool_use>"));
            }
            Block::ToolResult(content) => {
                parts.push(format!("<tool_result>{content}</tool_result>"));
            }
            Block::Reasoning => {}
        }
    }
    parts.join(" ")
}

/// The first `keep` characters of `text`, with a marker for what was cut.
fn clip(text: &str, keep: usize) -> String {
    let total = text.chars().count();
    if total <= keep {
        return text.to_string();
    }
    let mut clipped: String = text.chars().take(keep).collect();
    let _ = write!(clipped, " […{} more characters not shown]", total - keep);
    clipped
}

#[cfg(test)]
mod tests {
    use crate::evidence::Role;

    use super::*;

    /// A window of many small records renders within the budget plus the
    /// framing of the records that got a share of it: the older rest is one
    /// line, not a prefix each.
    #[test]
    fn a_window_of_many_small_records_renders_bounded() {
        let records: Vec<Message> = (0..100_000)
            .map(|i| Message {
                role: Role::Assistant,
                blocks: vec![crate::evidence::Block::Text(format!(
                    "record {i} says a thing"
                ))],
            })
            .collect();
        let rendered = render_window(&records);
        let chars = rendered.chars().count();
        // Every rendered record costs its allowance plus a framing of a few
        // dozen characters; the omission line is one more.
        assert!(
            chars < WINDOW_RENDER_BUDGET_CHARS * 3,
            "{chars} chars for 100 000 records"
        );
        assert!(rendered.starts_with('['), "{}", &rendered[..80]);
        assert!(
            rendered.contains("older records not shown"),
            "{}",
            &rendered[..80]
        );
        assert!(
            rendered.ends_with("record 99999 says a thing"),
            "{}",
            &rendered[rendered.len() - 80..]
        );
    }

    fn assistant(text: &str) -> Message {
        Message {
            role: Role::Assistant,
            blocks: vec![Block::Text(text.to_string())],
        }
    }

    fn tool_result(content: &str) -> Message {
        Message {
            role: Role::User,
            blocks: vec![Block::ToolResult(content.to_string())],
        }
    }

    #[test]
    fn the_judge_sees_tool_calls_and_tool_results_not_only_prose() {
        let window = vec![
            Message {
                role: Role::Assistant,
                blocks: vec![
                    Block::Reasoning,
                    Block::ToolUse {
                        name: "Read".to_string(),
                        input: "{\"file_path\":\"models.py\"}".to_string(),
                    },
                ],
            },
            tool_result("class Edition:\n    pass"),
            assistant("I see the class now."),
        ];
        let rendered = render_window(&window);
        assert_eq!(
            rendered,
            "[assistant] <tool_use name=\"Read\">{\"file_path\":\"models.py\"}</tool_use>\n\
             [user] <tool_result>class Edition:\n    pass</tool_result>\n\
             [assistant] I see the class now."
        );
    }

    #[test]
    fn a_long_record_is_clipped_with_a_visible_marker() {
        let long = "x".repeat(RECORD_RENDER_BUDGET_CHARS + 500);
        let rendered = render_window(&[tool_result(&long)]);
        assert!(rendered.contains("[…"));
        assert!(rendered.contains("more characters not shown]"));
        // The <tool_result> tag is part of the body and counts; what survives
        // is the budgeted head.
        assert!(rendered.chars().count() < RECORD_RENDER_BUDGET_CHARS + 100);
    }

    #[test]
    fn the_window_budget_is_spent_newest_first() {
        // Nine records at the per-record cap want 36 000; the window holds
        // 24 000. The newest six get their cap and use it up; the oldest
        // three are omitted, under one line, not rendered as a prefix each.
        let window: Vec<Message> = (0..9)
            .map(|i| tool_result(&format!("{i}").repeat(RECORD_RENDER_BUDGET_CHARS)))
            .collect();
        let rendered = render_window(&window);
        let lines: Vec<&str> = rendered.lines().collect();
        assert_eq!(lines.len(), 7);
        assert_eq!(lines[0], "[3 older records not shown]");
        assert!(lines[1].starts_with("[user] <tool_result>333"));
        assert!(lines[6].chars().count() >= RECORD_RENDER_BUDGET_CHARS);
        assert!(rendered.chars().count() < WINDOW_RENDER_BUDGET_CHARS + 7 * 80);

        // Seven records that leave the budget short of one: the oldest is
        // starved down to a glimpse, and nothing is omitted.
        let window: Vec<Message> = (0..7)
            .map(|i| tool_result(&format!("{i}").repeat(RECORD_RENDER_BUDGET_CHARS - 500)))
            .collect();
        let rendered = render_window(&window);
        let lines: Vec<&str> = rendered.lines().collect();
        assert_eq!(lines.len(), 7);
        assert!(lines[0].starts_with("[user] <tool_result>000"));
        assert!(lines[0].chars().count() < STARVED_RECORD_CHARS + 80);
        assert!(lines[1].chars().count() >= RECORD_RENDER_BUDGET_CHARS - 500);
    }

    #[test]
    fn the_judge_is_not_told_what_was_already_said_and_the_writer_is() {
        let evidence = vec![assistant("working")];
        let said = vec!["Maybe read the error first.".to_string()];
        let observation = Observation {
            task: "Fix it.",
            evidence: &evidence,
            said: &said,
        };
        let judge = judge_prompt(&observation, "CRITERION");
        assert!(judge.starts_with(
            "# Criterion\n\nCRITERION\n\n# The task the engineer was given\n\nFix it.\n\n"
        ));
        assert!(judge.contains("# What they have done, most recent last\n\n[assistant] working\n"));
        assert!(!judge.contains("already said"));
        assert!(!judge.contains("Maybe read the error first."));

        let writer = writer_prompt(&observation, "CRITERION");
        assert!(writer.starts_with(&judge));
        assert!(
            writer
                .ends_with("# What you have already said to them\n\nMaybe read the error first.\n")
        );
        let quiet = Observation {
            said: &[],
            ..observation
        };
        assert!(writer_prompt(&quiet, "CRITERION").ends_with("\n\n(nothing yet)\n"));
    }

    #[test]
    fn the_writer_instructions_carry_the_cap() {
        assert!(
            WRITER_INSTRUCTIONS.contains(&format!("At most {MAX_INTERVENTION_CHARS} characters"))
        );
    }
}
