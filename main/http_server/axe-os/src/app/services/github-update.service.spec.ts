import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  provideHttpClientTesting
} from '@angular/common/http/testing';

import { GithubRelease, GithubUpdateService } from './github-update.service';

describe('GithubUpdateService', () => {
  let service: GithubUpdateService;
  let httpTesting: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    service = TestBed.inject(GithubUpdateService);
    httpTesting = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpTesting.verify());

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  it('uses the fork release API as update source', () => {
    expect(service['baseReleasesUrl']).toBe(
      'https://api.github.com/repos/mane/ESP-Miner-NerdQAxePlus/releases'
    );
  });

  it('compares fork and NerdQAxe+ LTS revisions numerically', () => {
    expect(service.compareVersions('v1.1.1-mane.10', 'v1.1.1-mane.9')).toBe(1);
    expect(service.compareVersions(
      'v1.1.1-mane.6-nqa-lts2',
      'v1.1.1-mane.6-nqa-lts3'
    )).toBe(-1);
    expect(service.compareVersions(
      'v1.1.1-mane.6-nqa-lts3',
      'v1.1.1-mane.6-nqa-lts3'
    )).toBe(0);
  });

  it('orders release candidates below the corresponding final release', () => {
    expect(service.compareVersions('v1.2.0-rc2', 'v1.2.0')).toBe(-1);
    expect(service.compareVersions('v1.2.0', 'v1.2.0-rc2')).toBe(1);
  });

  it('finds the alternate NerdQAxe+ LTS factory asset label', () => {
    const release: GithubRelease = {
      id: 1,
      tag_name: 'v1.1.1-mane.6-nqa-lts3',
      name: 'NerdQAxe+ LTS 3',
      prerelease: false,
      body: '',
      published_at: '2026-07-19T00:00:00Z',
      assets: [{
        id: 2,
        name: 'esp-miner-factory-NerdQAxePlus-LTS-v1.1.1-mane.6-nqa-lts3.bin',
        browser_download_url: 'https://example.invalid/firmware.bin',
        size: 1,
      }],
    };

    expect(service.findFactoryAsset(
      release,
      'NerdQAxe+',
      'v1.1.1-mane.6-nqa-lts2'
    )?.name).toBe(
      'esp-miner-factory-NerdQAxePlus-LTS-v1.1.1-mane.6-nqa-lts3.bin'
    );
  });

  it('does not crossgrade an LTS device to a regular NerdQAxe+ release', () => {
    const release: GithubRelease = {
      id: 1,
      tag_name: 'v1.1.1-mane.7',
      name: 'regular release',
      prerelease: false,
      body: '',
      published_at: '2026-07-19T00:00:00Z',
      assets: [{
        id: 2,
        name: 'esp-miner-factory-NerdQAxe+-v1.1.1-mane.7.bin',
        browser_download_url: 'https://example.invalid/regular.bin',
        size: 1,
      }],
    };

    expect(service.findFactoryAsset(
      release,
      'NerdQAxe+',
      'v1.1.1-mane.6-nqa-lts2'
    )).toBeUndefined();
    expect(service.findFactoryAsset(release, 'NerdQAxe+', 'v1.1.1-mane.6')?.name).toBe(
      'esp-miner-factory-NerdQAxe+-v1.1.1-mane.7.bin'
    );
  });

  it('uses the LTS OTA filename only for an installed NerdQAxe+ LTS build', () => {
    expect(service.getFirmwareFilename('NerdQAxe+', 'v1.1.1-mane.6-nqa-lts2')).toBe(
      'esp-miner-NerdQAxePlus-LTS.bin'
    );
    expect(service.getFirmwareFilename('NerdQAxe+', 'v1.1.1-mane.6')).toBe(
      'esp-miner-NerdQAxe+.bin'
    );
  });

  it('marks the newest channel-specific release as latest', () => {
    const globalLatest: GithubRelease = {
      id: 1,
      tag_name: 'v1.1.1-mane.7',
      name: 'regular release',
      prerelease: false,
      body: '',
      published_at: '2026-07-19T01:00:00Z',
      assets: [],
    };
    const ltsLatest: GithubRelease = {
      ...globalLatest,
      id: 2,
      tag_name: 'v1.1.1-mane.6-nqa-lts3',
      name: 'LTS release',
      published_at: '2026-07-19T00:00:00Z',
    };

    service.getReleases(false, release => release.id === ltsLatest.id)
      .subscribe(releases => {
        expect(releases).toHaveSize(1);
        expect(releases[0].id).toBe(ltsLatest.id);
        expect(releases[0].isLatest).toBeTrue();
      });

    httpTesting.expectOne(
      'https://api.github.com/repos/mane/ESP-Miner-NerdQAxePlus/releases?per_page=50&page=1'
    ).flush([globalLatest, ltsLatest]);
    httpTesting.expectOne(
      'https://api.github.com/repos/mane/ESP-Miner-NerdQAxePlus/releases/latest'
    ).flush(globalLatest);
  });
});
