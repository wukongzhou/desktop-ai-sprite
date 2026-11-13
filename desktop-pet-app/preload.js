const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pet', {
  onState: (cb) => ipcRenderer.on('state', (_e, s) => cb(s)),
  poke: () => ipcRenderer.send('poke'),
  menu: () => ipcRenderer.send('menu'),
});
