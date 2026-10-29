import logging

from app.db.session import LocalSession
from app.services.document_storage import cleanup_orphaned_files


logging.basicConfig(level=logging.INFO)


def main() -> None:
    with LocalSession() as db:
        deleted, failed = cleanup_orphaned_files(db)

    print(f"Deleted {len(deleted)} orphaned document file(s).")
    if failed:
        print(f"Failed to delete {len(failed)} file(s): {', '.join(failed)}")


if __name__ == "__main__":
    main()
