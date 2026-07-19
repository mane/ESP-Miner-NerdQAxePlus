import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, EMPTY } from 'rxjs';
import { map, switchMap, expand, scan, takeWhile, last } from 'rxjs/operators';

export interface GithubAsset {
  id: number;
  name: string;
  browser_download_url: string;
  size: number;
}

export interface GithubRelease {
  id: number;
  tag_name: string;
  name: string;
  prerelease: boolean;
  body: string;
  published_at: string;
  assets: GithubAsset[];
  isLatest?: boolean;
}

export interface VersionComparison {
  isNewer: boolean;
  isSame: boolean;
  isOlder: boolean;
  currentVersion: string;
  latestVersion: string;
}

export enum UpdateStatus {
  UP_TO_DATE = 'up-to-date',
  UPDATE_AVAILABLE = 'update-available',
  OUTDATED = 'outdated',
  UNKNOWN = 'unknown'
}

@Injectable({
  providedIn: 'root'
})
export class GithubUpdateService {

  private readonly baseReleasesUrl =
    'https://api.github.com/repos/mane/ESP-Miner-NerdQAxePlus/releases';

  constructor(
    private httpClient: HttpClient
  ) { }

  /** Fetch a single page of releases */
  private fetchReleasePage(page: number, perPage = 50): Observable<GithubRelease[]> {
    const url = `${this.baseReleasesUrl}?per_page=${perPage}&page=${page}`;
    return this.httpClient.get<GithubRelease[]>(url);
  }

  /**
   * Fetch releases of ONE type (stable or prerelease) until we have
   * at least `targetCount` items or there are no more pages.
   */
  private loadReleasesOfType(
    includePrereleases: boolean,
    targetCount = 10,
    maxPages = 10,
    perPage = 50,
    releaseFilter: (release: GithubRelease) => boolean = () => true
  ): Observable<GithubRelease[]> {
    const isStable = (r: GithubRelease) =>
      !r.prerelease && !/-rc/i.test(r.tag_name);

    const isPre = (r: GithubRelease) =>
      r.prerelease || /-rc/i.test(r.tag_name);

    const matchesType = (release: GithubRelease) =>
      (includePrereleases ? isPre(release) : isStable(release)) && releaseFilter(release);

    // start with page 1
    return this.fetchReleasePage(1, perPage).pipe(
      expand((releases, index) => {
        const nextPage = index + 2; // index starts at 0 (page 1)
        const isLastPage = releases.length < perPage;
        const reachedMaxPages = nextPage > maxPages;

        if (isLastPage || reachedMaxPages) {
          return EMPTY;
        }

        return this.fetchReleasePage(nextPage, perPage);
      }),
      // accumulate only matching releases
      scan((acc, releases) => {
        const filtered = releases.filter(matchesType);
        return acc.concat(filtered);
      }, [] as GithubRelease[]),
      // solange weiter sammeln, bis wir genug haben
      takeWhile(acc => acc.length < targetCount, true),
      // am Ende letztes akkumuliertes Array liefern
      last(),
      // auf gewünschte Anzahl begrenzen
      map(acc => acc.slice(0, targetCount))
    );
  }

  /**
   * Fetch either:
   *  - up to 10 stable releases (includePrereleases = false)
   *  - up to 10 prereleases (includePrereleases = true)
   *
   * Es werden mehrere Seiten geladen, bis genug Releases vom gewünschten Typ
   * gefunden wurden oder keine Releases mehr da sind.
   */
  public getReleases(
    includePrereleases = false,
    releaseFilter: (release: GithubRelease) => boolean = () => true
  ): Observable<GithubRelease[]> {
    const latest$ = this.httpClient.get<GithubRelease>(
      `${this.baseReleasesUrl}/latest`
    );

    const selected$ = this.loadReleasesOfType(
      includePrereleases,
      10,
      10,
      50,
      releaseFilter
    );

    return selected$.pipe(
      switchMap((releases: GithubRelease[]) =>
        latest$.pipe(
          map((latest) => {
            // GitHub's /latest endpoint is repository-wide. On the LTS device
            // the newest matching release may therefore differ from it.
            const latestForChannel = releases.some(r => r.id === latest.id)
              ? latest.id
              : releases[0]?.id;

            return releases.map(r => ({
              ...r,
              body: r.body || '',
              isLatest: !includePrereleases && r.id === latestForChannel
            }));
          })
        )
      )
    );
  }

  /**
   * Compare firmware versions, including the fork/LTS suffixes used by this
   * repository (for example `v1.1.1-mane.6-nqa-lts2`).
   *
   * A plain numeric split is not sufficient here: it would consider `lts2`
   * and `lts3` equal and prevent the UI from advertising an LTS update.
   * Returns: 1 if v1 > v2, -1 if v1 < v2, 0 if equal
   */
  public compareVersions(v1: string, v2: string): number {
    const parsed1 = this.parseVersion(v1);
    const parsed2 = this.parseVersion(v2);

    const coreLength = Math.max(parsed1.core.length, parsed2.core.length);
    for (let i = 0; i < coreLength; i++) {
      const part1 = parsed1.core[i] ?? 0;
      const part2 = parsed2.core[i] ?? 0;
      if (part1 !== part2) {
        return part1 > part2 ? 1 : -1;
      }
    }

    const preReleaseNames = new Set(['alpha', 'beta', 'pre', 'preview', 'rc', 'dev']);
    const preReleaseIndex1 = parsed1.suffix.findIndex(
      part => typeof part === 'string' && preReleaseNames.has(part)
    );
    const preReleaseIndex2 = parsed2.suffix.findIndex(
      part => typeof part === 'string' && preReleaseNames.has(part)
    );

    const channel1 = preReleaseIndex1 < 0
      ? parsed1.suffix
      : parsed1.suffix.slice(0, preReleaseIndex1);
    const channel2 = preReleaseIndex2 < 0
      ? parsed2.suffix
      : parsed2.suffix.slice(0, preReleaseIndex2);
    const channelComparison = this.compareVersionTokens(channel1, channel2);
    if (channelComparison !== 0) {
      return channelComparison;
    }

    const isPreRelease1 = preReleaseIndex1 >= 0;
    const isPreRelease2 = preReleaseIndex2 >= 0;
    if (isPreRelease1 !== isPreRelease2) {
      return isPreRelease1 ? -1 : 1;
    }

    if (!isPreRelease1) {
      return 0;
    }

    return this.compareVersionTokens(
      parsed1.suffix.slice(preReleaseIndex1),
      parsed2.suffix.slice(preReleaseIndex2)
    );
  }

  private parseVersion(version: string): { core: number[]; suffix: Array<number | string> } {
    const normalized = (version ?? '').trim().replace(/^v/i, '');
    const match = normalized.match(/^(\d+(?:\.\d+)*)(.*)$/);
    const core = (match?.[1] ?? '0').split('.').map(part => Number(part));
    const suffix = (match?.[2] ?? normalized)
      .toLowerCase()
      .match(/[a-z]+|\d+/g)
      ?.map(part => /^\d+$/.test(part) ? Number(part) : part) ?? [];

    return { core, suffix };
  }

  private compareVersionTokens(
    tokens1: Array<number | string>,
    tokens2: Array<number | string>
  ): number {
    const length = Math.max(tokens1.length, tokens2.length);
    for (let i = 0; i < length; i++) {
      const part1 = tokens1[i];
      const part2 = tokens2[i];

      if (part1 === undefined || part2 === undefined) {
        return part1 === part2 ? 0 : part1 === undefined ? -1 : 1;
      }
      if (part1 === part2) {
        continue;
      }
      if (typeof part1 === 'number' && typeof part2 === 'number') {
        return part1 > part2 ? 1 : -1;
      }
      if (typeof part1 === 'number' || typeof part2 === 'number') {
        return typeof part1 === 'number' ? -1 : 1;
      }
      return part1 > part2 ? 1 : -1;
    }

    return 0;
  }

  /**
   * Compare current version with latest release
   */
  public getVersionComparison(currentVersion: string, latestRelease: GithubRelease): VersionComparison {
    const comparison = this.compareVersions(currentVersion, latestRelease.tag_name);

    return {
      isNewer: comparison > 0,
      isSame: comparison === 0,
      isOlder: comparison < 0,
      currentVersion,
      latestVersion: latestRelease.tag_name
    };
  }

  /**
   * Get update status based on version comparison
   */
  public getUpdateStatus(currentVersion: string, latestRelease: GithubRelease | null): UpdateStatus {
    if (!latestRelease) {
      return UpdateStatus.UNKNOWN;
    }

    const comparison = this.compareVersions(currentVersion, latestRelease.tag_name);

    if (comparison === 0) {
      return UpdateStatus.UP_TO_DATE;
    } else if (comparison < 0) {
      return UpdateStatus.UPDATE_AVAILABLE;
    } else {
      return UpdateStatus.OUTDATED;
    }
  }

  /**
   * Download firmware directly from GitHub
   */
  public downloadFirmware(url: string): Observable<any> {
    return this.httpClient.get(url, {
      responseType: 'blob',
      reportProgress: true,
      observe: 'events'
    });
  }

  /**
   * Get changelog formatted as HTML
   */
  public getChangelog(release: GithubRelease): string {
    if (!release.body) {
      return 'No changelog available';
    }

    // Convert markdown to basic HTML (simple conversion)
    return release.body
      .replace(/### (.*)/g, '<h4>$1</h4>')
      .replace(/## (.*)/g, '<h3>$1</h3>')
      .replace(/# (.*)/g, '<h2>$1</h2>')
      .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.*?)\*/g, '<em>$1</em>')
      .replace(/\n/g, '<br>');
  }

  /**
   * Find asset in release by filename
   */
  public findAsset(release: GithubRelease, filename: string): GithubAsset | undefined {
    return release.assets.find(asset => asset.name === filename);
  }

  /** Find the regular or LTS factory asset matching the device model. */
  public findFactoryAsset(
    release: GithubRelease,
    deviceModel: string,
    currentVersion = ''
  ): GithubAsset | undefined {
    const normalizedModel = this.normalizeDeviceModel(deviceModel);
    const labels = normalizedModel === 'NerdQAxe+'
      ? [/nqa-lts/i.test(currentVersion) ? 'NerdQAxePlus-LTS' : normalizedModel]
      : [normalizedModel];

    return labels
      .map(label => `esp-miner-factory-${label}-${release.tag_name}.bin`)
      .map(filename => this.findAsset(release, filename))
      .find((asset): asset is GithubAsset => asset !== undefined);
  }

  public getDefaultFactoryFilename(
    release: GithubRelease,
    deviceModel: string,
    currentVersion = ''
  ): string {
    const releaseLabel = this.getReleaseModelLabel(deviceModel, currentVersion);
    return `esp-miner-factory-${releaseLabel}-${release.tag_name}.bin`;
  }

  public getFirmwareFilename(deviceModel: string, currentVersion: string): string {
    const releaseLabel = this.getReleaseModelLabel(deviceModel, currentVersion);
    return `esp-miner-${releaseLabel}.bin`;
  }

  private getReleaseModelLabel(deviceModel: string, currentVersion: string): string {
    const normalizedModel = this.normalizeDeviceModel(deviceModel);
    return normalizedModel === 'NerdQAxe+' && /nqa-lts/i.test(currentVersion)
      ? 'NerdQAxePlus-LTS'
      : normalizedModel;
  }

  private normalizeDeviceModel(model: string): string {
    return (model ?? '').replace(/γ/g, 'Gamma').replace(/\s+/g, '');
  }


}
