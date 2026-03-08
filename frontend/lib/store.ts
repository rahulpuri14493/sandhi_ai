import { create } from 'zustand';
import type { User } from './types';
import { authAPI } from './api';

interface AuthState {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  loadUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isLoading: false,
  login: async (email: string, password: string) => {
    set({ isLoading: true });
    try {
      await authAPI.login(email, password);
      const user = await authAPI.getCurrentUser();
      set({ user, isLoading: false });
    } catch (error) {
      set({ isLoading: false });
      throw error;
    }
  },
  logout: () => {
    authAPI.logout();
    set({ user: null });
  },
  loadUser: async () => {
    set({ isLoading: true });
    try {
      const user = await authAPI.getCurrentUser();
      set({ user, isLoading: false });
    } catch (error) {
      set({ user: null, isLoading: false });
    }
  },
}));
