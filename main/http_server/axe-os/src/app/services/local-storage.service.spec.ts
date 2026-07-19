import { LocalStorageService } from './local-storage.service';

describe('LocalStorageService', () => {
  let service: LocalStorageService;

  beforeEach(() => {
    localStorage.clear();
    service = new LocalStorageService();
  });

  afterEach(() => localStorage.clear());

  it('returns null for malformed JSON instead of breaking the UI', () => {
    localStorage.setItem('broken-object', '{not-json');
    expect(service.getObject('broken-object')).toBeNull();
  });

  it('returns null for empty and non-finite numeric values', () => {
    localStorage.setItem('empty-number', '');
    localStorage.setItem('invalid-number', 'not-a-number');
    localStorage.setItem('infinite-number', 'Infinity');

    expect(service.getNumber('empty-number')).toBeNull();
    expect(service.getNumber('invalid-number')).toBeNull();
    expect(service.getNumber('infinite-number')).toBeNull();
  });

  it('round-trips finite numbers and objects', () => {
    service.setNumber('number', 42.5);
    service.setObject('object', { enabled: true });

    expect(service.getNumber('number')).toBe(42.5);
    expect(service.getObject<{ enabled: boolean }>('object')).toEqual({ enabled: true });
  });
});
