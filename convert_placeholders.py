"""One-time fix: turn placeholder entries into real "no code" entries.

Before the "No code needed" and "No password" buttons existed, people typed
'0000' for a restroom with no lock code, and 'no-password' or
'<open-network>' for an open Wi-Fi network. This script converts those rows.
Votes stay on the same row, so nothing is lost.

    python convert_placeholders.py --dry-run   # show what would change
    python convert_placeholders.py             # save the changes
"""
import argparse

from app import app, db, BathroomCode, WifiPassword, Vote, NO_PASSWORD_WORDS

BATHROOM_PLACEHOLDERS = {'0000'}


def merge_into(target, dup, item_type):
    """Move dup's votes onto target, then delete dup. A user who voted on
    both rows keeps only the vote on target."""
    target.votes += dup.votes
    for v in Vote.query.filter_by(item_id=dup.id, item_type=item_type).all():
        if Vote.query.filter_by(user_id=v.user_id, item_id=target.id, item_type=item_type).first():
            target.votes -= 1 if v.vote_type == 'upvote' else -1
            db.session.delete(v)
        else:
            v.item_id = target.id
    if dup.last_confirmed_at and (not target.last_confirmed_at
                                  or dup.last_confirmed_at > target.last_confirmed_at):
        target.last_confirmed_at = dup.last_confirmed_at
    db.session.delete(dup)


def convert(Model, field, is_placeholder, item_type):
    changed = 0
    for row in Model.query.filter_by(no_code=False).all():
        if not is_placeholder(getattr(row, field)):
            continue
        shop = row.coffee_shop
        existing = Model.query.filter(Model.coffee_shop_id == row.coffee_shop_id,
                                      Model.no_code.is_(True), Model.id != row.id).first()
        if existing:
            print(f"  merge {item_type} {row.id} {getattr(row, field)!r} into {existing.id} "
                  f"at {shop.name} (shop {shop.id})")
            merge_into(existing, row, item_type)
        else:
            print(f"  convert {item_type} {row.id} {getattr(row, field)!r} at {shop.name} "
                  f"(shop {shop.id}, {row.votes} votes)")
            setattr(row, field, '')
            row.no_code = True
        db.session.flush()
        changed += 1
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true', help='Show changes without saving.')
    args = parser.parse_args()
    with app.app_context():
        n = convert(BathroomCode, 'code', lambda v: v in BATHROOM_PLACEHOLDERS, 'bathroom_code')
        n += convert(WifiPassword, 'password', lambda v: v.lower() in NO_PASSWORD_WORDS,
                     'wifi_password')
        if args.dry_run:
            db.session.rollback()
            print(f"Dry run: {n} rows would change. Nothing saved.")
        else:
            db.session.commit()
            print(f"Saved: {n} rows changed.")


if __name__ == '__main__':
    main()
