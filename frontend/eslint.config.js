import js from '@eslint/js';
import reactPlugin from 'eslint-plugin-react';
import reactHooksPlugin from 'eslint-plugin-react-hooks';

export default [
    js.configs.recommended,
    {
        files: ['src/**/*.{js,jsx}'],
        languageOptions: {
            ecmaVersion: 2024,
            sourceType: 'module',
            parserOptions: {
                ecmaFeatures: {
                    jsx: true,
                },
            },
            globals: {
                document: 'readonly',
                navigator: 'readonly',
                window: 'readonly',
                console: 'readonly',
                setTimeout: 'readonly',
                clearTimeout: 'readonly',
                setInterval: 'readonly',
                clearInterval: 'readonly',
                process: 'readonly',
                URL: 'readonly',
                Promise: 'readonly',
                Event: 'readonly',
                fetch: 'readonly',
                AbortController: 'readonly',
                FormData: 'readonly',
                Audio: 'readonly',
                Blob: 'readonly',
                confirm: 'readonly',
                atob: 'readonly',
                localStorage: 'readonly',
                URLSearchParams: 'readonly'
            }
        },
        plugins: {
            react: reactPlugin,
            'react-hooks': reactHooksPlugin,
        },
        rules: {
            ...reactPlugin.configs.recommended.rules,
            ...reactHooksPlugin.configs.recommended.rules,
            'react/prop-types': 'off', // We're generally not using prop-types
            'react/react-in-jsx-scope': 'off', // React 17+ JSX transform doesn't require React in scope
            'react/no-unescaped-entities': 'off',
            'react/no-unknown-property': 'off',
            'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }]
        },
        settings: {
            react: {
                version: 'detect',
            },
        },
    },
];
