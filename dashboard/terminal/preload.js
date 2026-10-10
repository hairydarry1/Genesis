/**
 * Genesis Trading Terminal - Preload Script
 *
 * Exposes safe APIs to the renderer process via contextBridge.
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    // Window controls
    minimize: () => ipcRenderer.invoke('window-minimize'),
    maximize: () => ipcRenderer.invoke('window-maximize'),
    close: () => ipcRenderer.invoke('window-close'),

    // Backend
    getBackendPort: () => ipcRenderer.invoke('get-backend-port'),

    // Python logs
    onPythonLog: (callback) => ipcRenderer.on('python-log', (event, msg) => callback(msg)),
});
