/**
 * Shoreguard — UI-only Constants
 * Pure presentation mappings (status → CSS class, page labels).
 * All OpenShell-derived data (providers, agents, images) comes from the API.
 */

const SG = {
    badges: {
        phase: {
            ready: 'text-bg-success',
            provisioning: 'text-bg-warning',
            error: 'text-bg-danger',
            deleting: 'text-bg-secondary',
            unknown: 'text-bg-secondary',
        },
        approval: {
            pending: 'text-bg-warning',
            approved: 'text-bg-success',
            rejected: 'text-bg-danger',
        },
        gateway: {
            connected: 'text-bg-success',
            running: 'text-bg-info',
            unreachable: 'text-bg-warning',
            stopped: 'text-bg-secondary',
            offline: 'text-bg-danger',
        },
    },

    icons: {
        gatewayType: {
            local: { icon: 'pc-display', label: 'Local' },
            remote: { icon: 'globe', label: 'Remote' },
            cloud: { icon: 'cloud', label: 'Cloud' },
        },
    },

    pages: {
        labels: {
            dashboard: 'Dashboard',
            sandboxes: 'Sandboxes',
            policies: 'Policy Presets',
            wizard: 'New Sandbox',
            providers: 'Providers',
            gateway: 'Gateways',
        },
    },

    config: {
        healthCheckInterval: 10000,
        healthCheckFallback: 5000,
        toastDelay: 4000,
        approvalToastDelay: 10000,
        actionRefreshDelay: 2000,
        wsMaxBackoff: 30000,
        wsHeartbeatTimeout: 45000,
        wsMaxRetries: 20,
        logLinesDefault: 200,
        wizardStepDelay: 200,
    },
};
