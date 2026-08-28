# Numbers

Append only. Every metric gets an entry, written when it is produced, never reconstructed.

Each entry must carry all four:

1. **Date** the number was produced.
2. **The number**, with its uncertainty if it has one.
3. **The data** it ran on: which dataset, which split, how many rows, and whether that split had been scored before.
4. **The exact command** that produced it, copy-pasteable.

A number without a reproducing command does not go in this file.

Newest entries go at the bottom.

---

## Ground rules

- Report the operating point that was chosen and why, not the best one found afterwards.
- A held-out split is scored **once**. If it gets scored again, that is a new entry saying so, not an edit of the old one.
- If a number turns out to be unreproducible, it stays here with a correction appended below it, and the incident goes in [what-broke.md](what-broke.md). Numbers are never silently deleted.

---

## Entries

*None yet. No metric has been produced.*

The only quantities recorded so far are API probe observations, not measurements of anything we built. They live in api-probe.md (private working doc) and are deliberately not duplicated here.
