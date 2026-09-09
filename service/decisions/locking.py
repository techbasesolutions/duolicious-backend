"""Shared lock order for regular likes and super-likes.

Call inside READ COMMITTED, before checking quota, reciprocity or debiting.
Locking both people in id order serializes first reciprocal likes across workers
and gives the next statement a snapshot containing the previous writer's commit.
"""
def lock_like_members(tx, viewer_id: int, target_uuid: str) -> None:
    tx.execute(
        "SELECT id FROM person WHERE id = %(viewer)s OR uuid = uuid_or_null(%(target)s) "
        "ORDER BY id FOR UPDATE",
        dict(viewer=viewer_id, target=str(target_uuid)),
    ).fetchall()
