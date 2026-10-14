import { enableProdMode } from '@angular/core';
import { preInitApp } from '@eui/core';
import { environment } from './environments/environment';
import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';
import { appConfig } from './app/app.config';

if (environment.production) {
    enableProdMode();
}

preInitApp(environment).then(() =>
    bootstrapApplication(AppComponent, appConfig).catch((err) => console.error(err)));

declare global {
    interface Window {
        global: Window;
    }
}
window.global = window;
