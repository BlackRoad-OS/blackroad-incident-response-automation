#!/usr/bin/env python3
"""
Incident Response and On-Call Management System
Track incidents, alerts, and on-call schedules
"""

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import argparse


DB_PATH = Path.home() / ".blackroad" / "incidents.db"
TEAM = ["alexa", "alice", "octavia", "aria", "shellfish"]


@dataclass
class Alert:
    """Represents a monitoring alert"""
    id: str
    source: str
    message: str
    severity: str
    fired_at: str
    incident_id: Optional[str] = None


@dataclass
class Incident:
    """Represents an incident"""
    id: str
    title: str
    severity: str  # P1, P2, P3, P4
    status: str  # new, investigating, identified, monitoring, resolved
    assignee: str
    services: List[str] = field(default_factory=list)
    timeline: List[Dict[str, str]] = field(default_factory=list)
    created_at: str = ""
    resolved_at: Optional[str] = None
    postmortem: str = ""


class IncidentManager:
    """Incident Response and On-Call Management"""
    
    def __init__(self):
        """Initialize manager with database"""
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database schema"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                severity TEXT,
                status TEXT,
                assignee TEXT,
                services TEXT,
                timeline TEXT,
                created_at TEXT,
                resolved_at TEXT,
                postmortem TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                source TEXT,
                message TEXT,
                severity TEXT,
                fired_at TEXT,
                incident_id TEXT,
                FOREIGN KEY(incident_id) REFERENCES incidents(id)
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def create_incident(self, title: str, severity: str, services: List[str] = None) -> Incident:
        """Create a new incident"""
        incident_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        assignee = self.oncall_schedule()
        services = services or []
        
        incident = Incident(
            id=incident_id,
            title=title,
            severity=severity,
            status="new",
            assignee=assignee,
            services=services,
            timeline=[],
            created_at=now
        )
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO incidents 
            (id, title, severity, status, assignee, services, timeline, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (incident.id, incident.title, incident.severity, incident.status,
              incident.assignee, json.dumps(incident.services), 
              json.dumps(incident.timeline), incident.created_at))
        conn.commit()
        conn.close()
        
        return incident
    
    def assign(self, incident_id: str, assignee: str) -> bool:
        """Assign incident to person"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE incidents SET assignee = ? WHERE id = ?', (assignee, incident_id))
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    
    def update_status(self, incident_id: str, status: str) -> bool:
        """Update incident status"""
        valid_statuses = ["new", "investigating", "identified", "monitoring", "resolved"]
        if status not in valid_statuses:
            return False
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('UPDATE incidents SET status = ? WHERE id = ?', (status, incident_id))
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    
    def add_timeline_event(self, incident_id: str, event: str, author: str) -> bool:
        """Add timestamped event to timeline"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('SELECT timeline FROM incidents WHERE id = ?', (incident_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False
        
        timeline = json.loads(row[0])
        timeline.append({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "author": author
        })
        
        cursor.execute('UPDATE incidents SET timeline = ? WHERE id = ?',
                      (json.dumps(timeline), incident_id))
        conn.commit()
        conn.close()
        return True
    
    def resolve(self, incident_id: str, resolution_notes: str = "") -> bool:
        """Resolve incident"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        cursor.execute(
            'UPDATE incidents SET status = ?, resolved_at = ? WHERE id = ?',
            ("resolved", now, incident_id)
        )
        conn.commit()
        conn.close()
        return cursor.rowcount > 0
    
    def get_mttr(self, severity: Optional[str] = None) -> float:
        """Get mean time to resolve in minutes"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if severity:
            cursor.execute(
                '''SELECT AVG(
                    (strftime('%s', resolved_at) - strftime('%s', created_at)) / 60.0
                  ) FROM incidents 
                   WHERE status = 'resolved' AND severity = ?''',
                (severity,)
            )
        else:
            cursor.execute(
                '''SELECT AVG(
                    (strftime('%s', resolved_at) - strftime('%s', created_at)) / 60.0
                  ) FROM incidents 
                   WHERE status = 'resolved' '''
            )
        
        row = cursor.fetchone()
        conn.close()
        return row[0] or 0
    
    def get_active_incidents(self) -> List[Incident]:
        """Get active incidents"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT id, title, severity, status, assignee, services, timeline, created_at, resolved_at, postmortem
               FROM incidents WHERE status != 'resolved' ORDER BY created_at DESC'''
        )
        rows = cursor.fetchall()
        conn.close()
        
        return [
            Incident(
                id=row[0], title=row[1], severity=row[2], status=row[3],
                assignee=row[4], services=json.loads(row[5]),
                timeline=json.loads(row[6]), created_at=row[7],
                resolved_at=row[8], postmortem=row[9] or ""
            )
            for row in rows
        ]
    
    def auto_create_from_alert(self, alert_source: str, message: str, severity: str) -> Incident:
        """Create incident from alert"""
        incident = self.create_incident(f"Alert: {message}", severity)
        
        alert_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO alerts (id, source, message, severity, fired_at, incident_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (alert_id, alert_source, message, severity, datetime.now().isoformat(), incident.id))
        conn.commit()
        conn.close()
        
        return incident
    
    def generate_postmortem(self, incident_id: str) -> str:
        """Generate markdown postmortem template"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT title, severity, assignee, timeline, created_at, resolved_at FROM incidents WHERE id = ?',
            (incident_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return ""
        
        title, severity, assignee, timeline_json, created_at, resolved_at = row
        timeline = json.loads(timeline_json)
        
        postmortem = f"""# Incident Postmortem

## Summary
- **Title:** {title}
- **Severity:** {severity}
- **Assignee:** {assignee}
- **Duration:** {created_at} to {resolved_at}

## Timeline
"""
        for event in timeline:
            postmortem += f"- **{event['timestamp']}** ({event['author']}): {event['event']}\n"
        
        postmortem += """
## Root Cause Analysis
<!-- Add root cause here -->

## Impact
<!-- Describe impact here -->

## Action Items
<!-- List action items here -->

## Lessons Learned
<!-- Add lessons learned here -->
"""
        return postmortem
    
    def oncall_schedule(self) -> str:
        """Get rotating on-call person based on day of week"""
        from datetime import datetime
        day_of_week = datetime.now().weekday()
        return TEAM[day_of_week % len(TEAM)]
    
    def get_incident(self, incident_id: str) -> Optional[Incident]:
        """Get incident details"""
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, title, severity, status, assignee, services, timeline, created_at, resolved_at, postmortem FROM incidents WHERE id = ?',
            (incident_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        return Incident(
            id=row[0], title=row[1], severity=row[2], status=row[3],
            assignee=row[4], services=json.loads(row[5]),
            timeline=json.loads(row[6]), created_at=row[7],
            resolved_at=row[8], postmortem=row[9] or ""
        )


def main():
    """CLI interface"""
    parser = argparse.ArgumentParser(description="Incident Manager")
    subparsers = parser.add_subparsers(dest='command')
    
    # Active command
    active_parser = subparsers.add_parser('active', help='List active incidents')
    
    # Create command
    create_parser = subparsers.add_parser('create', help='Create incident')
    create_parser.add_argument('title', help='Incident title')
    create_parser.add_argument('severity', help='Severity (P1/P2/P3/P4)')
    create_parser.add_argument('services', nargs='*', help='Affected services')
    
    # Resolve command
    resolve_parser = subparsers.add_parser('resolve', help='Resolve incident')
    resolve_parser.add_argument('incident_id', help='Incident ID')
    resolve_parser.add_argument('--notes', help='Resolution notes')
    
    # Status command
    status_parser = subparsers.add_parser('status', help='Update incident status')
    status_parser.add_argument('incident_id', help='Incident ID')
    status_parser.add_argument('new_status', help='New status')
    
    # Timeline command
    timeline_parser = subparsers.add_parser('timeline', help='Add timeline event')
    timeline_parser.add_argument('incident_id', help='Incident ID')
    timeline_parser.add_argument('event', help='Event description')
    timeline_parser.add_argument('--author', default='system', help='Event author')
    
    # Postmortem command
    pm_parser = subparsers.add_parser('postmortem', help='Generate postmortem')
    pm_parser.add_argument('incident_id', help='Incident ID')
    
    # MTTR command
    mttr_parser = subparsers.add_parser('mttr', help='Get MTTR')
    mttr_parser.add_argument('--severity', help='Filter by severity')
    
    # Oncall command
    oncall_parser = subparsers.add_parser('oncall', help='Get oncall person')
    
    args = parser.parse_args()
    manager = IncidentManager()
    
    if args.command == 'active':
        incidents = manager.get_active_incidents()
        for inc in incidents:
            print(f"{inc.id} - {inc.title} [{inc.severity}] ({inc.status}) - {inc.assignee}")
    elif args.command == 'create':
        incident = manager.create_incident(args.title, args.severity, args.services)
        print(f"Created: {incident.id} - {incident.title} (assigned to {incident.assignee})")
    elif args.command == 'resolve':
        if manager.resolve(args.incident_id, args.notes or ""):
            print(f"Resolved: {args.incident_id}")
        else:
            print("Not found")
    elif args.command == 'status':
        if manager.update_status(args.incident_id, args.new_status):
            print(f"Updated: {args.incident_id} -> {args.new_status}")
        else:
            print("Not found or invalid status")
    elif args.command == 'timeline':
        if manager.add_timeline_event(args.incident_id, args.event, args.author):
            print(f"Added event to {args.incident_id}")
        else:
            print("Not found")
    elif args.command == 'postmortem':
        postmortem = manager.generate_postmortem(args.incident_id)
        print(postmortem)
    elif args.command == 'mttr':
        mttr = manager.get_mttr(args.severity)
        print(f"MTTR: {mttr:.2f} minutes")
    elif args.command == 'oncall':
        print(f"On-call: {manager.oncall_schedule()}")


if __name__ == '__main__':
    main()
