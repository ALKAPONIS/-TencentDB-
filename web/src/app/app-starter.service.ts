import { inject, Injectable } from '@angular/core';
import {
    UserService,
    I18nService,
    UserDetails,
    UserPreferences,
    EuiServiceStatus,
} from '@eui/core';
import { Observable, of } from 'rxjs';
import { switchMap } from 'rxjs/operators';

@Injectable({
    providedIn: 'root',
})
export class AppStarterService {
    defaultUserPreferences: UserPreferences | undefined;
    private userService: UserService = inject(UserService);
    private i18nService: I18nService = inject(I18nService);

    start(): Observable<EuiServiceStatus> {
        return this.initUserService().pipe(
            switchMap(() => {
                return this.i18nService.init();
            }),
        );
    }

    /**
     * Fetches user details,
     * create user: UserState object
     * then initialise to the UserService on run time
     */
    initUserService(): Observable<EuiServiceStatus> {
        return this.fetchUserDetails().pipe(
            switchMap((userDetails) => this.userService.init(userDetails))
        );
    }

    /**
     * The demo has no EU Login: every visitor is an anonymous, pseudonymous user.
     * The id that scopes long-term memory lives in the browser (see AssistantApiService).
     */
    private fetchUserDetails(): Observable<UserDetails> {
        return of({ userId: 'demo-user', firstName: 'Demo', lastName: 'User', fullName: 'Demo user' });
    }
}
