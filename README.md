# SCMS Pro — Smart Classroom Management System (Web Edition)

A full-featured, real-time web application for classroom management, upgraded from the original PyQt5 desktop app.

## 🚀 Features

| Feature | Details |
|---|---|
| **Dashboard** | Live stats, weekly chart, class-wise overview, activity feed |
| **Attendance** | One-click marking, QR scan, bulk mark, auto-refresh |
| **Timetable** | Interactive weekly grid, add/delete periods per class |
| **Students** | Add/delete with QR generation, credential management |
| **Reports** | Charts, cumulative data, performance badges |
| **Student Portal** | Personal attendance trend, timetable view |
| **Real-time** | Server-Sent Events (SSE) for live attendance updates |
| **Role-based Auth** | Admin, Teacher, Student roles |

## 🛠️ Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the app
```bash
python app.py
```

### 3. Open in browser
```
http://localhost:5000
```

## 🔐 Default Credentials

| Role | Username | Password |
|---|---|---|
| Admin | `admin` | `admin123` |
| Teacher | `teacher` | `teacher123` |
| Student | *auto-generated* | `1234` |

## 📁 Project Structure

```
scms_web/
├── app.py              ← Flask routes + SSE real-time
├── database.py         ← All DB operations (SQLite)
├── requirements.txt    ← Dependencies
├── scms.db             ← Auto-created SQLite database
├── qrcodes/            ← Student QR images
├── credentials/        ← Student credential files
└── templates/
    ├── base.html       ← Sidebar + topbar layout
    ├── login.html      ← Login page
    ├── dashboard.html  ← Main dashboard with charts
    ├── attendance.html ← Real-time attendance marking
    ├── students.html   ← Student management
    ├── timetable.html  ← Weekly timetable grid
    ├── reports.html    ← Analytics & reports
    └── student_portal.html ← Student self-service portal
```

## ⚡ Real-time Features

The app uses **Server-Sent Events (SSE)** for live updates:
- When attendance is marked → all open dashboards update instantly
- Live activity feed on dashboard
- Auto-refresh attendance table every 10 seconds
- Dashboard stats refresh every 15 seconds

## 🎨 Tech Stack

- **Backend**: Python + Flask
- **Database**: SQLite
- **Frontend**: Vanilla JS + Chart.js
- **Real-time**: SSE (EventSource)
- **QR Codes**: qrcode[pil]
- **Fonts**: Outfit + DM Mono (Google Fonts)
- **Icons**: Font Awesome 6
