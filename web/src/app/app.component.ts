import { Component } from '@angular/core';
import { TranslateModule } from '@ngx-translate/core';
import { EUI_LAYOUT } from '@eui/components/layout';

@Component({
    selector: 'app-root',
    templateUrl: './app.component.html',
    imports: [TranslateModule, ...EUI_LAYOUT],
})
export class AppComponent {}
