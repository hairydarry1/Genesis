/**
 * Genesis Trading Terminal - Electron Main Process
 *
 * Manages:
 *   1. Main window (frameless, dark theme)
 *   2. Python backend process
 *   3. IPC between frontend and backend
 *   4. System tray
 */

const { app, BrowserWindow, ipcMain, Tray, Menu, nativeTheme } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow = null;
let pythonProcess = null;
let tray = null;

const PYTHON_BACKEND_PORT = 8080;
const isDev = process.argv.includes('--dev');

// ---------------------------------------------------------------------------
// Python Backend
// ---------------------------------------------------------------------------
function startPythonBackend() {
    const backendPath = path.join(__dirname, 'server.py');
    console.log(`Starting Python backend: ${backendPath}`);

    pythonProcess = spawn('python', [backendPath], {
        cwd: path.join(__dirname, '..', '..'),
        stdio: ['ignore', 'pipe', 'pipe'],
    });

    pythonProcess.stdout.on('data', (data) => {
        const msg = data.toString().trim();
        if (msg) console.log(`[Python] ${msg}`);
        // Forward to renderer
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('python-log', msg);
        }
    });

    pythonProcess.stderr.on('data', (data) => {
        const msg = data.toString().trim();
        if (msg) console.error(`[Python ERR] ${msg}`);
    });

    pythonProcess.on('close', (code) => {
        console.log(`Python backend exited with code ${code}`);
        pythonProcess = null;
    });

    pythonProcess.on('error', (err) => {
        console.error(`Failed to start Python backend: ${err.message}`);
    });
}

function stopPythonBackend() {
    if (pythonProcess) {
        pythonProcess.kill();
        pythonProcess = null;
    }
}

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------
function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1400,
        height: 900,
        minWidth: 1000,
        minHeight: 600,
        frame: false,
        backgroundColor: '#131722',
        titleBarStyle: 'hidden',
        icon: undefined,
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    // In dev mode, load from Vite dev server
    if (isDev) {
        mainWindow.loadURL('http://localhost:3000');
    } else {
        mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));
    }

    // Open DevTools in dev mode
    if (isDev) {
        mainWindow.webContents.openDevTools();
    }

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

// ---------------------------------------------------------------------------
// System Tray
// ---------------------------------------------------------------------------
function createTray() {
    // Use a simple 16x16 icon
    try {
        tray = new Tray(path.join(__dirname, 'icon.png'));
        const contextMenu = Menu.buildFromTemplate([
            { label: 'Show Terminal', click: () => mainWindow && mainWindow.show() },
            { label: 'Hide', click: () => mainWindow && mainWindow.hide() },
            { type: 'separator' },
            { label: 'Quit', click: () => app.quit() },
        ]);
        tray.setToolTip('Genesis Trading Terminal');
        tray.setContextMenu(contextMenu);
        tray.on('double-click', () => { mainWindow && mainWindow.show(); });
    } catch(e) {
        console.log('Tray icon not found, skipping tray');
    }
}

// ---------------------------------------------------------------------------
// IPC Handlers
// ---------------------------------------------------------------------------
ipcMain.handle('window-minimize', () => mainWindow && mainWindow.minimize());
ipcMain.handle('window-maximize', () => {
    if (mainWindow) {
        mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
    }
});
ipcMain.handle('window-close', () => mainWindow && mainWindow.close());
ipcMain.handle('get-backend-port', () => PYTHON_BACKEND_PORT);

// ---------------------------------------------------------------------------
// App Lifecycle
// ---------------------------------------------------------------------------
app.whenReady().then(() => {
    nativeTheme.themeSource = 'dark';
    startPythonBackend();
    // Wait a moment for Python backend to start before creating window
    setTimeout(() => {
        createWindow();
        createTray();
    }, 2000);
});

app.on('window-all-closed', () => {
    stopPythonBackend();
    app.quit();
});

app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
    }
});

app.on('before-quit', () => {
    stopPythonBackend();
});
