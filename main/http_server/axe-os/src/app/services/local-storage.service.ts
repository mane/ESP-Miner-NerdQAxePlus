import { Injectable } from '@angular/core';

@Injectable({
  providedIn: 'root'
})
export class LocalStorageService {

  constructor() { }

  setItem(key: string, value: any) {
    localStorage.setItem(key, value);
  }

  getItem(key: string): any {
    return localStorage.getItem(key);
  }

  setBool(key: string, value: boolean) {
    localStorage.setItem(key, String(value));
  }

  getBool(key: string): boolean {
    return localStorage.getItem(key) === 'true';
  }

  setObject(key: string, value: object) {
    localStorage.setItem(key, JSON.stringify(value));
  }

  getObject<T = any>(key: string): T | null {
    const item = localStorage.getItem(key);
    if (item == null || item.length < 1) {
      return null;
    }

    try {
      return JSON.parse(item) as T;
    } catch {
      return null;
    }
  }

  setNumber(key: string, value: number) {
    localStorage.setItem(key, value.toString());
  }

  getNumber(key: string): number | null {
    const value = localStorage.getItem(key);
    if (value === null || value.trim() === '') {
      return null;
    }

    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
}
