const { contextBridge, ipcRenderer } = require('electron');

// Expose protected methods to renderer
contextBridge.exposeInMainWorld('electronAPI', {
    // Directory operations
    openDirectoryDialog: () => ipcRenderer.invoke('open-directory-dialog'),
    setDirectory: (path) => ipcRenderer.invoke('set-directory', path),
    getCurrentDirectory: () => ipcRenderer.invoke('get-current-directory'),

    // File operations
    listFiles: (path) => ipcRenderer.invoke('list-files', path),
    readFile: (path) => ipcRenderer.invoke('read-file', path),
    saveFile: (path, content) => ipcRenderer.invoke('save-file', path, content),
    getFilePath: (path) => ipcRenderer.invoke('get-file-path', path),
    getFileMtime: (path) => ipcRenderer.invoke('get-file-mtime', path),

    // Terminal operations
    createTerminal: (termId, options) => ipcRenderer.invoke('create-terminal', termId, options),
    closeTerminal: (termId) => ipcRenderer.invoke('close-terminal', termId),
    sendTerminalInput: (termId, data) => ipcRenderer.send('terminal-input', termId, data),
    resizeTerminal: (termId, cols, rows) => ipcRenderer.send('terminal-resize', termId, cols, rows),

    // Tmux
    listTmuxSessions: () => ipcRenderer.invoke('list-tmux-sessions'),

    // Event listeners
    onDirectoryOpened: (callback) => ipcRenderer.on('directory-opened', (event, data) => callback(data)),
    onTerminalOutput: (callback) => ipcRenderer.on('terminal-output', (event, data) => callback(data)),
    onTerminalClosed: (callback) => ipcRenderer.on('terminal-closed', (event, data) => callback(data)),

    // Utility
    isElectron: true
});
