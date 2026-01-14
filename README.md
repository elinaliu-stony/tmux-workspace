# Tmux Workspace

A web-based terminal manager for tmux sessions, similar to JupyterLab but focused on terminal management.

## Features

- **Multiple terminals**: Open multiple bash shells or tmux sessions in a single browser window
- **Drag-and-drop layout**: Arrange terminals using GoldenLayout (drag tabs, split panels)
- **Tmux integration**: Attach to existing tmux sessions from the browser
- **Layout persistence**: Your panel arrangement is saved and restored on page reload
- **Dark theme**: Easy on the eyes for long terminal sessions

## Architecture

```
Browser (xterm.js + GoldenLayout)
    ↓ WebSocket (Socket.IO)
Flask + Flask-SocketIO
    ↓ pty.fork()
tmux attach / bash
    ↓
SQLite (layout persistence)
```

## Installation

```bash
# Clone the repository
git clone https://github.com/elinaliu-stony/tmux-workspace.git
cd tmux-workspace

# Install dependencies
pip install -r requirements.txt

# Run the server
python app.py
```

Open http://localhost:5001 in your browser.

## Usage

- **+ Bash**: Open a new bash terminal
- **+ Tmux**: Connect to an existing tmux session
- **Save Layout**: Manually save your panel arrangement (also auto-saves)
- **Drag tabs**: Rearrange or split terminal panels

## Requirements

- Python 3.8+
- tmux (for tmux session features)
- macOS or Linux (uses PTY)

## Tech Stack

- **Backend**: Flask, Flask-SocketIO, Python pty module
- **Frontend**: xterm.js 4.19, GoldenLayout 1.5.9 (CDN)
- **Storage**: SQLite for layout persistence

## License

MIT
