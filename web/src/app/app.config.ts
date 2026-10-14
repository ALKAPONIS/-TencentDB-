import { inject, provideAppInitializer } from '@angular/core';
import { ApplicationConfig, importProvidersFrom } from '@angular/core';
import { provideRouter } from '@angular/router';
import { Observable } from 'rxjs';

import { routes } from './app.routes';
import { AppStarterService } from './app-starter.service';

import { TranslateModule } from '@ngx-translate/core';

import { HTTP_INTERCEPTORS, provideHttpClient, withInterceptorsFromDi } from '@angular/common/http';
import {
    CachePreventionInterceptor,
    CorsSecurityInterceptor,
    CsrfPreventionInterceptor,
    EuLoginSessionTimeoutHandlingInterceptor,
    CoreModule as EuiCoreModule,
    translateConfig,
    EUI_CONFIG_TOKEN,
    provideEuiInitializer,
    EuiServiceStatus,
} from '@eui/core';

import { appConfig as euiAppConfig} from '../config';
import { environment } from '../environments/environment';
import { provideAnimations } from '@angular/platform-browser/animations';

/**
 * The provided function is injected at application startup and executed during
 * app initialization. If the function returns a Promise or an Observable, initialization
 * does not complete until the Promise is resolved or the Observable is completed.
 */
const init = (): Observable<EuiServiceStatus> => {
    const appStarter = inject(AppStarterService);
    return appStarter.start();
};

export const appConfig: ApplicationConfig = {
    providers: [
        {
            provide: EUI_CONFIG_TOKEN,
            useValue: { appConfig: euiAppConfig, environment }
        },
        {
            // Sets the withCredentials on Ajax Request to send the JSESSIONID cookie to another domain.
            // This is necessary when a request is being made to another domain that is protected by EU Login.
            provide: HTTP_INTERCEPTORS,
            useClass: CorsSecurityInterceptor,
            multi: true,
        },
        {
            // When the authentication session is invalid, we need to re-authenticate. The browser refreshes the current URL,
            // and lets the EU Login client redirect to the official EU Login page.
            provide: HTTP_INTERCEPTORS,
            useClass: EuLoginSessionTimeoutHandlingInterceptor,
            multi: true,
        },
        {
            // Adds HTTP header to each Ajax request that ensures the request is set by a piece of JavaScript code in the application.
            // This prevents dynamically-loaded content from forging a request in the name of the currently logged-in user.
            // Be aware that this assumes that cross-site scripting (XSS) is already put in place, (default setting in Angular).
            provide: HTTP_INTERCEPTORS,
            useClass: CsrfPreventionInterceptor,
            multi: true,
        },
        {
            // Asks the intermediate proxies not to return a cache copy of the resource.
            // In matter of fact forces each server in the chain to validate the freshness of the resource.
            provide: HTTP_INTERCEPTORS,
            useClass: CachePreventionInterceptor,
            multi: true,
        },
        provideEuiInitializer(),
        provideAppInitializer(init),
        provideHttpClient(withInterceptorsFromDi()),
        importProvidersFrom(
            EuiCoreModule.forRoot(),
            TranslateModule.forRoot(translateConfig)
        ),
        AppStarterService,
        provideRouter(routes),
        provideAnimations(),
    ],
};
