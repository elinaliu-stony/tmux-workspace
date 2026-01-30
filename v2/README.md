# Tmux Workspace

A web-based terminal manager with tmux integration, featuring workspace groups for organizing your terminals by project or topic.

## Features

- **Workspace Groups** - Organize terminals into groups (like browser tabs)
- **Tmux Integration** - Attach to existing tmux sessions from your browser
- **Drag & Drop Layout** - Arrange terminals with GoldenLayout
- **Persistent Layouts** - Each group remembers its panel arrangement
- **Token Authentication** - Secure access with random tokens (like Jupyter)
- **Dark Theme** - Easy on the eyes

## Installation

```bash
pip install tmux-workspace
```

Or install from source:

```bash
git clone https://github.com/elinaliu-stony/tmux-workspace.git
cd tmux-workspace/v2
pip install -e .
```

## Usage

```bash
# Start the server (generates random access token)
tmux-workspace

# Specify port
tmux-workspace --port 8080

# Disable authentication
tmux-workspace --no-token

# Show help
tmux-workspace --help
```

After starting, open the URL shown in the terminal. Use the access token to log in.

## Requirements

- Python 3.8+
- tmux (for tmux session integration)
- A modern web browser

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Format code
black tmux_workspace/
```

## License

MIT
