const { app, BrowserWindow } = require('electron');

function createWindow() {
  const win = new BrowserWindow({
    width: 480,
    height: 780,
    title: '翻译鹦鹉',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true
    }
  });
  win.loadURL('https://5130599.best/Translator/');
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
