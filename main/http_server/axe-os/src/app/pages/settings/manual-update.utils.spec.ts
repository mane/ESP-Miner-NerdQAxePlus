import {
  MANUAL_WWW_UPDATE_PENDING_KEY,
  WWW_IMAGE_SIZE_BYTES,
  WWW_IDENTITY_MAGIC,
  clearPendingManualWwwUpdate,
  hasPendingManualWwwUpdate,
  markManualWwwUpdatePending,
  validateWebsiteImage,
} from './manual-update.utils';

describe('manual update helpers', () => {
  const image = (
    name: string,
    version = 'v1.1.1-mane.6-nqa-lts7',
    size = WWW_IMAGE_SIZE_BYTES,
    commit = 'ad96b01',
  ) => {
    const contents = new Uint8Array(size);
    const identity = new TextEncoder().encode(
      `${WWW_IDENTITY_MAGIC}\nVERSION_TAG=${version}\nCOMMIT_HASH=${commit}\n`,
    );
    if (identity.length <= contents.length) contents.set(identity, 512);
    return {
      name,
      size,
      arrayBuffer: async () => contents.buffer,
    };
  };

  describe('validateWebsiteImage', () => {
    it('accepts the official unversioned WWW image only with a matching identity', async () => {
      expect(await validateWebsiteImage(image('www.bin'), 'v1.1.1-mane.6-nqa-lts7')).toEqual({
        valid: true,
        embeddedVersion: 'v1.1.1-mane.6-nqa-lts7',
        identityCommit: 'ad96b01',
      });
    });

    it('accepts a narrowly versioned image matching its identity and running firmware', async () => {
      expect(
        await validateWebsiteImage(
          image('www-v1.1.1-mane.6-nqa-lts7.bin'),
          'v1.1.1-mane.6-nqa-lts7',
        ),
      ).toEqual({
        valid: true,
        embeddedVersion: 'v1.1.1-mane.6-nqa-lts7',
        identityCommit: 'ad96b01',
      });
    });

    it('accepts the canonical NerdQAxePlus LTS archive asset', async () => {
      expect((await validateWebsiteImage(
        image('www-NerdQAxePlus-LTS-v1.1.1-mane.6-nqa-lts7.bin'),
        'v1.1.1-mane.6-nqa-lts7',
      )).valid).toBeTrue();
    });

    it('also accepts an underscore separator without weakening the version grammar', async () => {
      expect((await validateWebsiteImage(
        image('www_1.2.3-rc.1.bin', 'v1.2.3-rc.1'),
        'v1.2.3-rc.1',
      )).valid).toBeTrue();
    });

    it('rejects arbitrary, path-like and double-extension names', async () => {
      for (const name of [
        'website.bin',
        'www-backup.bin',
        '../www.bin',
        'www-v1.2.3.bin.exe',
        'www-NerdQAxePlus-LTS-backup.bin',
        'www-nerdqaxeplus-lts-v1.2.3.bin',
      ]) {
        expect((await validateWebsiteImage(image(name, 'v1.2.3'), 'v1.2.3')).error)
          .withContext(name)
          .toBe('filename');
      }
    });

    it('rejects an official www.bin whose identity belongs to another release', async () => {
      expect(
        await validateWebsiteImage(
          image('www.bin', 'v1.1.1-mane.6-nqa-lts6'),
          'v1.1.1-mane.6-nqa-lts7',
        ),
      ).toEqual({
        valid: false,
        error: 'version',
        embeddedVersion: 'v1.1.1-mane.6-nqa-lts6',
        identityCommit: 'ad96b01',
      });
    });

    it('rejects a versioned filename that disagrees with the embedded identity', async () => {
      expect((await validateWebsiteImage(
        image('www-v1.1.1-mane.6-nqa-lts6.bin'),
        'v1.1.1-mane.6-nqa-lts7',
      )).error).toBe('version');
      expect((await validateWebsiteImage(
        image('www-NerdQAxePlus-LTS-v1.1.1-mane.6-nqa-lts6.bin'),
        'v1.1.1-mane.6-nqa-lts7',
      )).error).toBe('version');
    });

    it('compares release identities case-sensitively like the firmware backend', async () => {
      expect((await validateWebsiteImage(
        image('www.bin', 'v1.1.1-mane.6-NQA-LTS7'),
        'v1.1.1-mane.6-nqa-lts7',
      )).error).toBe('version');
    });

    it('rejects files that do not match the exact WWW partition size', async () => {
      expect((await validateWebsiteImage(
        image('www.bin', 'v1.2.3', WWW_IMAGE_SIZE_BYTES - 1),
        'v1.2.3',
      )).error)
        .toBe('size');
      expect((await validateWebsiteImage(
        image('www.bin', 'v1.2.3', WWW_IMAGE_SIZE_BYTES + 1),
        'v1.2.3',
      )).error)
        .toBe('size');
    });

    it('fails closed when the identity is missing or duplicated', async () => {
      const missing = image('www.bin');
      missing.arrayBuffer = async () => new ArrayBuffer(WWW_IMAGE_SIZE_BYTES);
      expect((await validateWebsiteImage(missing, 'v1.1.1-mane.6-nqa-lts7')).error)
        .toBe('identity');

      const duplicated = image('www.bin');
      const duplicateBuffer = new Uint8Array(await duplicated.arrayBuffer());
      duplicateBuffer.copyWithin(2048, 512, 600);
      duplicated.arrayBuffer = async () => duplicateBuffer.buffer;
      expect((await validateWebsiteImage(duplicated, 'v1.1.1-mane.6-nqa-lts7')).error)
        .toBe('identity');
    });

    it('rejects malformed or unbounded identity commit values', async () => {
      for (const commit of ['bad/commit', 'x'.repeat(65)]) {
        expect((await validateWebsiteImage(
          image('www.bin', 'v1.1.1-mane.6-nqa-lts7', WWW_IMAGE_SIZE_BYTES, commit),
          'v1.1.1-mane.6-nqa-lts7',
        )).error)
          .withContext(commit)
          .toBe('identity');
      }
    });
  });

  describe('pending WWW update state', () => {
    let storage: Storage;

    beforeEach(() => {
      const values = new Map<string, string>();
      storage = {
        get length() { return values.size; },
        clear: () => values.clear(),
        getItem: key => values.get(key) ?? null,
        key: index => Array.from(values.keys())[index] ?? null,
        removeItem: key => { values.delete(key); },
        setItem: (key, value) => { values.set(key, value); },
      };
    });

    it('survives a page reload until the paired WWW upload succeeds', () => {
      markManualWwwUpdatePending(storage, 'esp-miner-NerdQAxePlus-LTS.bin');

      expect(hasPendingManualWwwUpdate(storage)).toBeTrue();
      expect(storage.getItem(MANUAL_WWW_UPDATE_PENDING_KEY)).toContain('esp-miner-NerdQAxePlus-LTS.bin');

      clearPendingManualWwwUpdate(storage);
      expect(hasPendingManualWwwUpdate(storage)).toBeFalse();
    });

    it('fails closed without crashing on corrupt storage data', () => {
      storage.setItem(MANUAL_WWW_UPDATE_PENDING_KEY, '{not-json');
      expect(hasPendingManualWwwUpdate(storage)).toBeFalse();
    });
  });
});
