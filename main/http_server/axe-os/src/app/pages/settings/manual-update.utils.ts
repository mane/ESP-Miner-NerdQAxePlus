export const WWW_IMAGE_SIZE_BYTES = 3 * 1024 * 1024;
export const WWW_IDENTITY_MAGIC = 'NERDQAXEPLUS_WEB_IDENTITY_V1';

export const MANUAL_WWW_UPDATE_PENDING_KEY = 'axe-os.manual-update.www-pending.v1';

export type WebsiteImageValidationError = 'filename' | 'size' | 'identity' | 'version';

export interface WebsiteImageValidationResult {
  valid: boolean;
  error?: WebsiteImageValidationError;
  embeddedVersion?: string;
  identityCommit?: string;
}

export interface WebsiteImageIdentity {
  version: string;
  commit: string;
}

type WebsiteImageFile = Pick<File, 'name' | 'size' | 'arrayBuffer'>;

interface ManualWwwUpdateState {
  pending: true;
  firmwareFilename: string;
  startedAt: number;
}

// Keep this deliberately narrow. Besides the generic www.bin asset, accept the
// canonical archived release name and the historic short alias only when their
// suffix is a SemVer-like release tag.
const RELEASE_VERSION_CAPTURE = '(v?\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?(?:\\+[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?)';
const VERSIONED_WWW_FILENAMES = [
  new RegExp(`^www-NerdQAxePlus-LTS-${RELEASE_VERSION_CAPTURE}\\.bin$`),
  new RegExp(`^www[-_]${RELEASE_VERSION_CAPTURE}\\.bin$`),
];
const IDENTITY_COMMIT = /^[0-9A-Za-z._-]{1,64}$/;

function normalizeVersion(version: string): string {
  return version.trim().replace(/^v(?=\d)/, '');
}

function extractFilenameVersion(filename: string): string | undefined {
  for (const pattern of VERSIONED_WWW_FILENAMES) {
    const match = pattern.exec(filename);
    if (match) return match[1];
  }
  return undefined;
}

function asciiBytes(value: string): Uint8Array {
  return Uint8Array.from(value, character => character.charCodeAt(0));
}

function findBytes(contents: Uint8Array, expected: Uint8Array, start = 0): number {
  const lastStart = contents.length - expected.length;
  for (let offset = start; offset <= lastStart; offset++) {
    let matches = true;
    for (let index = 0; index < expected.length; index++) {
      if (contents[offset + index] !== expected[index]) {
        matches = false;
        break;
      }
    }
    if (matches) return offset;
  }
  return -1;
}

function readIdentityValue(contents: Uint8Array, start: number): { value: string; next: number } | null {
  let value = '';
  for (let offset = start; offset < contents.length; offset++) {
    const byte = contents[offset];
    if (byte === 0x0a) {
      return value ? { value, next: offset + 1 } : null;
    }
    // Identity values are generated as printable, whitespace-free ASCII.
    if (value.length >= 128 || byte < 0x21 || byte > 0x7e) return null;
    value += String.fromCharCode(byte);
  }
  return null;
}

export function extractWebsiteImageIdentity(buffer: ArrayBuffer): WebsiteImageIdentity | null {
  const contents = new Uint8Array(buffer);
  const prefix = asciiBytes(`${WWW_IDENTITY_MAGIC}\nVERSION_TAG=`);
  const first = findBytes(contents, prefix);
  if (first < 0 || findBytes(contents, prefix, first + 1) >= 0) return null;

  const version = readIdentityValue(contents, first + prefix.length);
  if (!version) return null;

  const commitPrefix = asciiBytes('COMMIT_HASH=');
  if (findBytes(contents, commitPrefix, version.next) !== version.next) return null;
  const commit = readIdentityValue(contents, version.next + commitPrefix.length);
  if (!commit || !IDENTITY_COMMIT.test(commit.value)) return null;

  return { version: version.value, commit: commit.value };
}

export async function validateWebsiteImage(
  file: WebsiteImageFile,
  runningFirmwareVersion: string,
): Promise<WebsiteImageValidationResult> {
  if (file.name.length > 160) {
    return { valid: false, error: 'filename' };
  }

  let embeddedVersion: string | undefined;
  if (file.name !== 'www.bin') {
    embeddedVersion = extractFilenameVersion(file.name);
    if (!embeddedVersion) {
      return { valid: false, error: 'filename' };
    }
  }

  if (file.size !== WWW_IMAGE_SIZE_BYTES) {
    return { valid: false, error: 'size', embeddedVersion };
  }

  let identity: WebsiteImageIdentity | null;
  try {
    identity = extractWebsiteImageIdentity(await file.arrayBuffer());
  } catch {
    return { valid: false, error: 'identity', embeddedVersion };
  }
  if (!identity) {
    return { valid: false, error: 'identity', embeddedVersion };
  }

  if (
    embeddedVersion &&
    normalizeVersion(embeddedVersion) !== normalizeVersion(identity.version)
  ) {
    return { valid: false, error: 'version', embeddedVersion };
  }

  if (
    !runningFirmwareVersion.trim() ||
    identity.version !== runningFirmwareVersion.trim()
  ) {
    return {
      valid: false,
      error: 'version',
      embeddedVersion: identity.version,
      identityCommit: identity.commit,
    };
  }

  return {
    valid: true,
    embeddedVersion: identity.version,
    identityCommit: identity.commit,
  };
}

export function hasPendingManualWwwUpdate(storage: Storage): boolean {
  try {
    const raw = storage.getItem(MANUAL_WWW_UPDATE_PENDING_KEY);
    if (!raw) {
      return false;
    }

    const state = JSON.parse(raw) as Partial<ManualWwwUpdateState>;
    return state.pending === true;
  } catch {
    return false;
  }
}

export function markManualWwwUpdatePending(storage: Storage, firmwareFilename: string): void {
  const state: ManualWwwUpdateState = {
    pending: true,
    firmwareFilename,
    startedAt: Date.now(),
  };

  try {
    storage.setItem(MANUAL_WWW_UPDATE_PENDING_KEY, JSON.stringify(state));
  } catch {
    // The guidance still remains active in memory if browser storage is blocked.
  }
}

export function clearPendingManualWwwUpdate(storage: Storage): void {
  try {
    storage.removeItem(MANUAL_WWW_UPDATE_PENDING_KEY);
  } catch {
    // Storage may be unavailable in privacy-restricted browser contexts.
  }
}
