# Agent Instructions (HouseHunder)

This file documents project-specific operating rules for automated agents and maintainers.

## Commit Prep Requests

- When asked to prepare commits, provide:
    - a pair of `git add ...` commands for related change sets
    - corresponding `git commit -S -F <message-file>` commands
    - commit message content aligned with `.gitmessage` format
- Default response format for this request: a single fenced code block so changes can be reviewed and run manually.

## Branch Change Summary Requests

- If asked for branch change summary:
    - default base branch is `development` unless user specifies otherwise
    - generate an untracked log file with commit messages from base to current HEAD
    - command pattern: `git log <base-hash>~..<head-hash> > <untracked-file>`
- Optionally generate an untracked PR summary markdown covering major features, fixes, risks, and test notes.
