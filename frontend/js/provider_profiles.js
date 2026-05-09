/**
 * Shoreguard — Provider Profiles registry page (M37 / OpenShell PR #1170).
 */

function providerProfilesPage() {
    return {
        loading: true,
        error: '',
        profiles: [],

        // Import dialog state
        importOpen: false,
        importText: '',
        importBusy: false,
        importDiagnostics: [],
        importLastValid: null,

        async load() {
            this.loading = true;
            this.error = '';
            try {
                const resp = await apiFetch(`${API}/provider-profiles`);
                this.profiles = (resp && resp.items) || [];
            } catch (e) {
                this.error = e.message;
            } finally {
                this.loading = false;
            }
        },

        openImportDialog() {
            this.importText = '';
            this.importDiagnostics = [];
            this.importLastValid = null;
            this.importOpen = true;
        },

        _parseImportText() {
            try {
                const parsed = JSON.parse(this.importText);
                if (!Array.isArray(parsed)) {
                    throw new Error('Expected a JSON array of {profile, source} items.');
                }
                return parsed;
            } catch (e) {
                throw new Error(`Invalid JSON: ${e.message}`);
            }
        },

        async lintProfiles() {
            this.importBusy = true;
            this.importDiagnostics = [];
            this.importLastValid = null;
            try {
                const profiles = this._parseImportText();
                const resp = await apiFetch(`${API}/provider-profiles/lint`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profiles }),
                });
                this.importDiagnostics = resp.diagnostics || [];
                this.importLastValid = !!resp.valid;
            } catch (e) {
                this.importLastValid = false;
                this.importDiagnostics = [{ severity: 'error', message: e.message }];
            } finally {
                this.importBusy = false;
            }
        },

        async applyProfiles() {
            this.importBusy = true;
            try {
                const profiles = this._parseImportText();
                const resp = await apiFetch(`${API}/provider-profiles/import`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ profiles }),
                });
                this.importDiagnostics = resp.diagnostics || [];
                if (resp.imported) {
                    showToast(`Imported ${(resp.profiles || []).length} profile(s).`, 'success');
                    this.importOpen = false;
                    await this.load();
                } else {
                    showToast('Import rejected — see diagnostics.', 'warning');
                    this.importLastValid = false;
                }
            } catch (e) {
                showToast(`Import failed: ${e.message}`, 'danger');
            } finally {
                this.importBusy = false;
            }
        },

        async deleteProfile(profileId) {
            const confirmed = await showConfirm(
                `Delete provider profile "${profileId}"? Custom profiles only — built-in profiles cannot be removed.`,
                { icon: 'trash', iconColor: 'text-danger', btnClass: 'btn-danger', btnLabel: 'Delete' },
            );
            if (!confirmed) return;
            try {
                const resp = await apiFetch(`${API}/provider-profiles/${profileId}`, {
                    method: 'DELETE',
                });
                if (resp.deleted) {
                    showToast(`Profile "${profileId}" deleted.`, 'success');
                    await this.load();
                } else {
                    showToast(`Profile "${profileId}" was not removed (built-in?).`, 'info');
                }
            } catch (e) {
                showToast(`Delete failed: ${e.message}`, 'danger');
            }
        },
    };
}
