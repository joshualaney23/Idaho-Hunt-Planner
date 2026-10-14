# Schema migrations

`planner/schema.sql` is schema **version 1** (DESIGN.md §5). Every later change is a file
here named `NNN_short_name.sql` (`002_add_x.sql`, `003_…`), applied in number order by
`planner.db.migrate()` on startup, each in one transaction that also bumps
`schema_version`. Never edit a migration after it has shipped; add a new one, and update
DESIGN.md §5 in the same PR. Numbers must be unique and greater than 1.
