const { app, BrowserWindow, ipcMain, dialog, Menu, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');

// Handle node-pty native module
let pty;
try {
    pty = require('node-pty');
} catch (e) {
    console.error('node-pty not available:', e.message);
}

let mainWindow;
let currentDirectory = null;
const terminals = new Map();

// Allowed file extensions
const ALLOWED_EXTENSIONS = ['.tex', '.bib', '.sty', '.cls', '.txt', '.md', '.pdf', '.jpg', '.jpeg', '.png', '.gif', '.svg'];
const TEXT_EXTENSIONS = ['.tex', '.bib', '.sty', '.cls', '.txt', '.md'];

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 800,
        minHeight: 600,
        title: 'TeX Workspace',
        backgroundColor: '#1e1e1e',
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js'),
            webviewTag: true,  // Enable <webview> for embedding external sites
            webSecurity: true,
            allowRunningInsecureContent: false
        }
    });

    mainWindow.loadFile(path.join(__dirname, 'index.html'));

    // Open DevTools in development
    // mainWindow.webContents.openDevTools();

    mainWindow.on('closed', () => {
        mainWindow = null;
        // Clean up terminals
        terminals.forEach((term, id) => {
            try { term.kill(); } catch (e) {}
        });
        terminals.clear();
    });
}

// Create menu
function createMenu() {
    const template = [
        {
            label: 'File',
            submenu: [
                {
                    label: 'Open Directory...',
                    accelerator: 'CmdOrCtrl+O',
                    click: async () => {
                        const result = await dialog.showOpenDialog(mainWindow, {
                            properties: ['openDirectory']
                        });
                        if (!result.canceled && result.filePaths.length > 0) {
                            currentDirectory = result.filePaths[0];
                            mainWindow.webContents.send('directory-opened', {
                                path: currentDirectory,
                                name: path.basename(currentDirectory)
                            });
                        }
                    }
                },
                { type: 'separator' },
                { role: 'quit' }
            ]
        },
        {
            label: 'Edit',
            submenu: [
                { role: 'undo' },
                { role: 'redo' },
                { type: 'separator' },
                { role: 'cut' },
                { role: 'copy' },
                { role: 'paste' },
                { role: 'selectAll' }
            ]
        },
        {
            label: 'View',
            submenu: [
                { role: 'reload' },
                { role: 'forceReload' },
                { role: 'toggleDevTools' },
                { type: 'separator' },
                { role: 'resetZoom' },
                { role: 'zoomIn' },
                { role: 'zoomOut' },
                { type: 'separator' },
                { role: 'togglefullscreen' }
            ]
        },
        {
            label: 'Window',
            submenu: [
                { role: 'minimize' },
                { role: 'zoom' },
                { role: 'close' }
            ]
        },
        {
            label: '+Web',
            submenu: [
                {
                    label: 'My Home',
                    click: () => { shell.openExternal('http://localhost/~elinaliu/'); }
                },
                {
                    label: 'ICDM Paper',
                    click: () => { shell.openExternal('http://localhost/~elinaliu/imbalance_icdm/'); }
                },
                { type: 'separator' },
                {
                    label: 'Claude Usage',
                    click: () => { shell.openExternal('https://claude.ai/settings/usage'); }
                }
            ]
        }
    ];

    const menu = Menu.buildFromTemplate(template);
    Menu.setApplicationMenu(menu);
}

app.whenReady().then(() => {
    createWindow();
    createMenu();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

// Handle graceful quit - tell renderer to save state first
app.on('before-quit', (event) => {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('save-state-before-quit');
    }
});

// IPC to trigger save from external script
ipcMain.handle('force-save-state', async () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('save-state-request');
        return { status: 'ok' };
    }
    return { status: 'no-window' };
});

// --- IPC Handlers ---

// Open directory dialog
ipcMain.handle('open-directory-dialog', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ['openDirectory']
    });
    if (!result.canceled && result.filePaths.length > 0) {
        currentDirectory = result.filePaths[0];
        return { path: currentDirectory, name: path.basename(currentDirectory) };
    }
    return null;
});

// Set directory directly
ipcMain.handle('set-directory', async (event, dirPath) => {
    const expandedPath = dirPath.replace(/^~/, os.homedir());
    const fullPath = path.resolve(expandedPath);

    if (fs.existsSync(fullPath) && fs.statSync(fullPath).isDirectory()) {
        currentDirectory = fullPath;
        return { path: currentDirectory, name: path.basename(currentDirectory) };
    }
    return { error: 'Directory not found' };
});

// Get current directory
ipcMain.handle('get-current-directory', () => {
    return currentDirectory ? { path: currentDirectory, name: path.basename(currentDirectory) } : null;
});

// List files
ipcMain.handle('list-files', async (event, relativePath = '') => {
    if (!currentDirectory) {
        return { error: 'No directory opened', files: [] };
    }

    const fullPath = relativePath
        ? path.join(currentDirectory, relativePath)
        : currentDirectory;

    // Security check
    if (!fullPath.startsWith(currentDirectory)) {
        return { error: 'Invalid path', files: [] };
    }

    try {
        const entries = fs.readdirSync(fullPath, { withFileTypes: true });
        const files = entries
            .filter(entry => !entry.name.startsWith('.'))
            .filter(entry => {
                if (entry.isDirectory()) return true;
                const ext = path.extname(entry.name).toLowerCase();
                return ALLOWED_EXTENSIONS.includes(ext);
            })
            .map(entry => {
                const ext = path.extname(entry.name).toLowerCase();
                return {
                    name: entry.name,
                    path: relativePath ? path.join(relativePath, entry.name) : entry.name,
                    isDirectory: entry.isDirectory(),
                    isText: !entry.isDirectory() && TEXT_EXTENSIONS.includes(ext),
                    isPdf: ext === '.pdf',
                    isImage: ['.jpg', '.jpeg', '.png', '.gif', '.svg'].includes(ext)
                };
            })
            .sort((a, b) => {
                if (a.isDirectory !== b.isDirectory) return a.isDirectory ? -1 : 1;
                return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
            });

        return { path: relativePath, root: currentDirectory, files };
    } catch (e) {
        return { error: e.message, files: [] };
    }
});

// Read file
ipcMain.handle('read-file', async (event, relativePath) => {
    if (!currentDirectory || !relativePath) {
        return { error: 'Invalid request' };
    }

    const fullPath = path.join(currentDirectory, relativePath);

    if (!fullPath.startsWith(currentDirectory)) {
        return { error: 'Invalid path' };
    }

    try {
        const content = fs.readFileSync(fullPath, 'utf-8');
        return { path: relativePath, content, type: 'text' };
    } catch (e) {
        return { error: e.message };
    }
});

// Save file
ipcMain.handle('save-file', async (event, relativePath, content) => {
    if (!currentDirectory || !relativePath) {
        return { error: 'Invalid request' };
    }

    const fullPath = path.join(currentDirectory, relativePath);

    if (!fullPath.startsWith(currentDirectory)) {
        return { error: 'Invalid path' };
    }

    try {
        fs.writeFileSync(fullPath, content, 'utf-8');
        return { status: 'ok', path: relativePath };
    } catch (e) {
        return { error: e.message };
    }
});

// Get file path for raw access (PDF, images)
ipcMain.handle('get-file-path', async (event, relativePath) => {
    if (!currentDirectory || !relativePath) {
        return null;
    }
    const fullPath = path.join(currentDirectory, relativePath);
    if (!fullPath.startsWith(currentDirectory) || !fs.existsSync(fullPath)) {
        return null;
    }
    return fullPath;
});

// Get file modification time
ipcMain.handle('get-file-mtime', async (event, relativePath) => {
    if (!currentDirectory || !relativePath) {
        return { mtime: null };
    }
    const fullPath = path.join(currentDirectory, relativePath);
    if (!fullPath.startsWith(currentDirectory)) {
        return { mtime: null };
    }
    try {
        const stat = fs.statSync(fullPath);
        return { mtime: stat.mtimeMs, path: relativePath };
    } catch (e) {
        return { mtime: null };
    }
});

// --- Terminal IPC ---

ipcMain.handle('create-terminal', async (event, termId, options = {}) => {
    if (!pty) {
        return { error: 'Terminal not available' };
    }

    const cwd = currentDirectory || os.homedir();

    // Determine shell command based on options
    let shell, args;
    if (options.type === 'tmux' && options.session) {
        shell = '/opt/homebrew/bin/tmux';
        args = ['attach', '-t', options.session];
    } else {
        shell = process.env.SHELL || '/bin/bash';
        args = ['-l'];
    }

    try {
        const term = pty.spawn(shell, args, {
            name: 'xterm-256color',
            cols: options.cols || 80,
            rows: options.rows || 24,
            cwd: cwd,
            env: process.env
        });

        terminals.set(termId, term);

        term.onData(data => {
            if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
                mainWindow.webContents.send('terminal-output', { termId, data });
            }
        });

        term.onExit(() => {
            terminals.delete(termId);
            if (mainWindow && !mainWindow.isDestroyed() && mainWindow.webContents && !mainWindow.webContents.isDestroyed()) {
                mainWindow.webContents.send('terminal-closed', { termId });
            }
        });

        return { status: 'ok', termId };
    } catch (e) {
        return { error: e.message };
    }
});

ipcMain.on('terminal-input', (event, termId, data) => {
    const term = terminals.get(termId);
    if (term) {
        try {
            term.write(data);
        } catch (e) {
            console.error('Terminal write error:', e);
            terminals.delete(termId);
        }
    }
});

ipcMain.on('terminal-resize', (event, termId, cols, rows) => {
    const term = terminals.get(termId);
    if (term) {
        try {
            term.resize(cols, rows);
        } catch (e) {
            console.error('Terminal resize error:', e);
            terminals.delete(termId);
        }
    }
});

ipcMain.handle('close-terminal', async (event, termId) => {
    const term = terminals.get(termId);
    if (term) {
        term.kill();
        terminals.delete(termId);
    }
    return { status: 'ok' };
});

// List tmux sessions
ipcMain.handle('list-tmux-sessions', async () => {
    const { exec } = require('child_process');
    return new Promise(resolve => {
        exec('/opt/homebrew/bin/tmux list-sessions -F "#{session_name}:#{session_windows}:#{session_attached}"', (error, stdout) => {
            if (error) {
                resolve([]);
                return;
            }
            const sessions = stdout.trim().split('\n')
                .filter(line => line)
                .map(line => {
                    const [name, windows, attached] = line.split(':');
                    return { name, windows: parseInt(windows), attached: attached === '1' };
                });
            resolve(sessions);
        });
    });
});
