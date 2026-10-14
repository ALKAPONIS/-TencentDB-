import { Routes } from '@angular/router';

export const routes: Routes = [
    { path: '', loadComponent: () => import('./features/assistant/assistant.component').then((m) => m.AssistantComponent) },
    { path: '**', redirectTo: '' },
];
