import { EuiEnvConfig } from '@eui/core';

interface EnvConfig extends EuiEnvConfig {
    production: boolean;
}

export const environment: EnvConfig = {
    production: false,
    enableDevToolRedux: true,
    envDynamicConfig: {
        uri: 'assets/env-json-config.json',
        deepMerge: true,
        merge: ['modules'],
    },
};
