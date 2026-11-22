#!/usr/bin/env python3
"""
Adds sample reports and sample citizen accounts so the site shows realistic numbers.

Add sample data:      python3 backend/seed_demo.py
Remove sample data:   python3 backend/seed_demo.py --remove

Sample reports use IDs CP-2001 to CP-2030. Sample accounts use the email domain
demo.civicpulse.local. --remove deletes only these, never real reports or accounts.
Works with both SQLite and MySQL, using the same DATABASE_URL setting as the server.
"""
import os
import random
import secrets
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402  (reuses the server's database layer and password hashing)

DEMO_ID_START, DEMO_ID_END = 2001, 2100
DEMO_DOMAIN = 'demo.civicpulse.local'
DEMO_CITIZENS = 25
DEMO_REPORTS = 30

PLAN = (['Roads'] * 8 + ['Waste Management'] * 6 + ['Streetlights'] * 5 +
        ['Water Supply'] * 5 + ['Drainage'] * 4 + ['Other'] * 2)

TITLES = {
    'Roads': ['Pothole near bus stop', 'Large pothole after rain', 'Road caved in near junction',
              'Broken divider on flyover', 'Potholes on service road', 'Damaged speed breaker',
              'Uneven road surface', 'Pothole causing traffic jam'],
    'Waste Management': ['Garbage not collected for three days', 'Overflowing dustbin near market',
                         'Dead animal on roadside', 'Construction debris dumped on footpath',
                         'Waste burning near houses', 'Dustbin missing from colony'],
    'Streetlights': ['Streetlight not working', 'Flickering streetlight in lane',
                     'Dark park entrance at night', 'Broken pole with exposed wires',
                     'Streetlight on during the day'],
    'Water Supply': ['Water pipeline leakage', 'No water supply for two days',
                     'Very low water pressure', 'Dirty smelling water supply',
                     'Overflowing overhead tank'],
    'Drainage': ['Blocked drain after rain', 'Open manhole cover', 'Sewage flowing onto road',
                 'Broken drain cover'],
    'Other': ['Encroachment on footpath', 'Stray dogs near school'],
}

LOCALITIES = ['Arera Colony', 'MP Nagar', 'Shivaji Nagar', 'Kolar Road', 'Habibganj', 'New Market',
              'Berasia Road', 'Ashoka Garden', 'Hamidia Road', 'Kohefiza', 'TT Nagar', 'Bairagarh',
              'Indrapuri', 'Govindpura', 'Peergate', 'Karond', 'Misrod', 'Ayodhya Nagar']

FIRST_NAMES = ['Asha', 'Ravi', 'Priya', 'Amit', 'Sunita', 'Rahul', 'Neha', 'Vikram', 'Pooja', 'Arjun',
               'Kavita', 'Sanjay', 'Meena', 'Deepak', 'Anita', 'Manoj', 'Rekha', 'Suresh', 'Geeta',
               'Anil', 'Shweta', 'Rajesh', 'Lata', 'Nitin', 'Sonal']
LAST_NAMES = ['Verma', 'Sharma', 'Patel', 'Singh', 'Yadav', 'Mishra', 'Tiwari', 'Joshi', 'Jain',
              'Gupta', 'Rathore', 'Dubey', 'Chouhan', 'Pandey', 'Sahu']


def iso(dt):
    return dt.isoformat(timespec='seconds')


def remove_demo(db):
    db.execute('DELETE FROM issues WHERE id BETWEEN ? AND ?', (DEMO_ID_START, DEMO_ID_END))
    db.execute('DELETE FROM users WHERE email LIKE ?', ('%@' + DEMO_DOMAIN,))
    db.commit()


def add_demo(db):
    existing = db.execute('SELECT COUNT(*) AS n FROM issues WHERE id BETWEEN ? AND ?',
                          (DEMO_ID_START, DEMO_ID_END)).fetchone()['n']
    if existing:
        raise SystemExit('Sample data is already added. Run with --remove first if you want to reset it.')

    rng = random.Random(2026)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = iso(now)

    # Sample citizen accounts. Their passwords are random, so nobody can sign in as them.
    citizen_ids = []
    for i in range(DEMO_CITIZENS):
        name = '%s %s' % (rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES))
        email = 'citizen%02d@%s' % (i + 1, DEMO_DOMAIN)
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, 'citizen', ?)",
            (name, email, server.hash_password(secrets.token_urlsafe(24)), stamp))
        citizen_ids.append(cur.lastrowid)

    # Sample reports, spread over the last few months.
    categories = PLAN[:]
    rng.shuffle(categories)
    statuses = ['Pending'] * 10 + ['In Progress'] * 8 + ['Resolved'] * 12
    rng.shuffle(statuses)

    for offset, (category, status) in enumerate(zip(categories, statuses)):
        issue_id = DEMO_ID_START + offset
        title = rng.choice(TITLES[category])
        locality = rng.choice(LOCALITIES)
        reported = now - timedelta(days=rng.randint(2, 110), hours=rng.randint(0, 12))
        description = ('Residents of %s have reported this problem: %s. '
                       'Please inspect the site and arrange the repair.') % (locality, title.lower())

        events = [('Pending', 'Report received.', reported)]
        if status in ('In Progress', 'Resolved'):
            events.append(('In Progress', 'Assigned to the local team.', reported + timedelta(days=rng.randint(1, 4))))
        if status == 'Resolved':
            events.append(('Resolved', 'Work completed.', events[-1][2] + timedelta(days=rng.randint(3, 12))))
        updated = events[-1][2]
        if updated > now:
            updated = now

        db.execute(
            'INSERT INTO issues (id, title, description, category, locality, status, photo_url, reporter_id, '
            'created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)',
            (issue_id, title, description, category, locality, status,
             rng.choice(citizen_ids), iso(reported), iso(updated)))
        for ev_status, note, when in events:
            db.execute('INSERT INTO issue_events (issue_id, status, note, created_at) VALUES (?, ?, ?, ?)',
                       (issue_id, ev_status, note, iso(min(when, now))))
    db.commit()


def main(argv):
    server.init_db()
    with closing(server.Db()) as db:
        if '--remove' in argv:
            remove_demo(db)
            print('Sample reports and sample accounts removed. Your real data was not touched.')
        else:
            add_demo(db)
            print('Added %d sample reports and %d sample citizen accounts.' % (DEMO_REPORTS, DEMO_CITIZENS))
            print('To remove them later: python3 backend/seed_demo.py --remove')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))