import { create } from 'zustand';

export interface ProviderSummary {
  type: 'api-key' | 'oauth';
  auth_type: 'api-key' | 'oauth' | 'none' | 'optional';
  display_name: string;
  base_url: string;
  api: string;
  api_key: string;
  models: (string | { id: string; [key: string]: any })[];
  custom_models: string[];
  has_credentials: boolean;
  supports_oauth: boolean;
  can_delete: boolean;
  config_status?: 'ok' | 'needs_setup' | 'invalid';
  config_error?: string | null;
  missing_fields?: string[];
}

interface ProvidersState {
  providersSummary: Record<string, ProviderSummary>;
  selectedProviderId: string | null;
  toastMessage: string;
  toastType: 'success' | 'error' | '';
  toastVisible: boolean;
  toolModel: string | null;
  heavyModel: string | null;
}

interface ProvidersActions {
  setState: (partial: Partial<ProvidersState>) => void;
  showToast: (message: string, type: 'success' | 'error') => void;
}

let _toastTimer: ReturnType<typeof setTimeout> | null = null;

export const useProvidersStore = create<ProvidersState & ProvidersActions>()((set) => ({
  providersSummary: {},
  selectedProviderId: null,
  toastMessage: '',
  toastType: '',
  toastVisible: false,
  toolModel: localStorage.getItem('lumen_tool_model') || null,
  heavyModel: localStorage.getItem('lumen_heavy_model') || null,

  setState: (partial) => set(partial),

  showToast: (message, type) => {
    if (_toastTimer) clearTimeout(_toastTimer);
    set({ toastMessage: message, toastType: type, toastVisible: true });
    _toastTimer = setTimeout(() => {
      set({ toastVisible: false });
    }, 1500);
  },
}));
