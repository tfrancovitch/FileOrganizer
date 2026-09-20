# P3.R11 — Query Examples

Open any `fixture.sqlite` in a SQLite browser.

```sql
SELECT * FROM p3_decision ORDER BY rowid;
SELECT * FROM p3_policy_version ORDER BY policy_id, version_no;
SELECT * FROM p3_bulk_member ORDER BY batch_id, target_ref;
SELECT * FROM p3_review_event ORDER BY rowid;
SELECT * FROM p3_plan_revision;
SELECT * FROM p3_plan_item;
SELECT key, value_json FROM expected_projection ORDER BY key;
```

`expected_projection` is test-oracle data. It is **not** a production recommendation for storing current projections.
