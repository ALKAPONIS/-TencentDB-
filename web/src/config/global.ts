import { GlobalConfig } from '@eui/core';

export const GLOBAL: GlobalConfig = {
    appTitle: 'EC Virtual Assistant',
    i18n: {
        i18nService: {
            defaultLanguage: 'en',
            languages: [{ code: 'en', label: 'English' }],
        },
        i18nLoader: {
            i18nFolders: ['i18n-eui', 'i18n'],
        },
    },
    user: {
        defaultUserPreferences: {
            dashboard: {},
            lang: 'en',
        },
    },
};
