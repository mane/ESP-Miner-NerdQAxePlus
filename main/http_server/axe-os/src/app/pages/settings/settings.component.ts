import { HttpEventType } from '@angular/common/http';
import { Component, OnInit, OnDestroy } from '@angular/core';
import { FormControl } from '@angular/forms';
import { catchError, combineLatest, Observable, of, shareReplay, startWith, Subscription, interval } from 'rxjs';
import { switchMap, tap, take } from 'rxjs/operators';
import { GithubUpdateService, UpdateStatus, VersionComparison, GithubRelease } from '../../services/github-update.service';
import { LoadingService } from '../../services/loading.service';
import { SystemService } from '../../services/system.service';
import { eASICModel } from '../../models/enum/eASICModel';
import { NbToastrService } from '@nebular/theme';
import { TranslateService } from '@ngx-translate/core';
import { IUpdateStatus } from 'src/app/models/IUpdateStatus';
import { OtpAuthService, EnsureOtpResult, EnsureOtpOptions } from '../../services/otp-auth.service';
import { ISettingsV2 } from '../../models/ISettingsV2';
import { getAppVersion } from 'src/app/app.module';
import {
  clearPendingManualWwwUpdate,
  hasPendingManualWwwUpdate,
  markManualWwwUpdatePending,
  validateWebsiteImage,
} from './manual-update.utils';

@Component({
  selector: 'app-settings',
  templateUrl: './settings.component.html',
  styleUrls: ['./settings.component.scss']
})
export class SettingsComponent implements OnInit, OnDestroy {

  public firmwareUpdateProgress: number = 0;
  public websiteUpdateProgress: number = 0;

  public deviceModel: string = "";
  public devToolsOpen: boolean = false;

  get isDangerZone(): boolean {
    const level = Number(localStorage.getItem('support-level') ?? 0);
    return level >= 1;
  }
  public eASICModel = eASICModel;
  public asicModel!: eASICModel;

  public expectedFileName: string = "";
  public expectedFactoryFilename: string = "";

  public selectedFirmwareFile: File | null = null;
  public selectedWebsiteFile: File | null = null;

  public info$: Observable<ISettingsV2>;

  public isWebsiteUploading = false;
  public isFirmwareUploading = false;
  public isOneClickUpdate = false;

  private updateStatusSub?: Subscription;
  private sawRebooting = false;
  private consecutiveStatusErrors = 0;
  private otaErrorShown = false;

  public currentStep: string = "";

  // New properties for enhanced update system
  public updateStatus: UpdateStatus = UpdateStatus.UNKNOWN;
  public UpdateStatus = UpdateStatus; // Make enum available in template
  public versionComparison: VersionComparison | null = null;
  public showChangelog: boolean = false;
  public changelog: string = '';
  public currentVersion: string = '';
  public currentWebVersion: string = '';
  public manualWwwUpdatePending: boolean = false;

  public otpEnabled: boolean = false;

  // Enhanced progress tracking
  public otaProgress: number = 0;
  private rebootCheckInterval?: any;

  public keepConfigCtrl = new FormControl<boolean>(true);
  public includePrereleasesCtrl = new FormControl<boolean>(false);
  public releases$!: Observable<GithubRelease[]>;   // list shown in dropdown
  public selectedRelease: GithubRelease | null = null;
  private latestStableRelease: GithubRelease | null = null;

  constructor(
    private systemService: SystemService,
    private toastrService: NbToastrService,
    private loadingService: LoadingService,
    private githubUpdateService: GithubUpdateService,
    private translate: TranslateService,
    private otpAuth: OtpAuthService,
  ) {
    this.info$ = this.systemService.getSettingsV2().pipe(
      shareReplay({ refCount: true, bufferSize: 1 })
    );
  }

  ngOnInit() {
    this.info$.pipe(this.loadingService.lockUIUntilComplete())
      .subscribe(info => {
        this.currentVersion = info.version;
        this.currentWebVersion = this.getAppVersion();
        //this.deviceModel = "NerdQAxe++";
        this.deviceModel = info.deviceModel;
        this.asicModel = info.asicModel;
        this.otpEnabled = !!info.otp;
        this.syncManualWwwUpdateState();

        this.expectedFileName = this.githubUpdateService.getFirmwareFilename(
          this.deviceModel,
          this.currentVersion
        );

        console.log('Device model from API:', this.deviceModel);
        console.log('Expected filename:', this.expectedFileName);

        // Update version status after we have both current version and latest release
        this.updateVersionStatus();
      });

    // Build releases$ AFTER info$ is available, and filter by asset existence
    this.releases$ = combineLatest([
      this.includePrereleasesCtrl.valueChanges.pipe(startWith(this.includePrereleasesCtrl.value)),
      this.info$
    ]).pipe(
      switchMap(([include, info]) =>
        this.githubUpdateService.getReleases(
          include,
          r =>
            !!this.githubUpdateService.findFactoryAsset(r, info.deviceModel, info.version)
        ).pipe(
          tap(list => {
            if (!include) {
              this.latestStableRelease = list.find(release => release.isLatest) ?? list[0] ?? null;
              this.updateVersionStatus();
            }
          })
        )
      ),
      tap(list => {
        if (!this.selectedRelease || !list.find(r => r.id === this.selectedRelease!.id)) {
          this.selectedRelease = list[0] ?? null;
          this.updateSelectedReleaseDeps();
        }
      }),
      shareReplay({ refCount: true, bufferSize: 1 })
    );


    this.checkUpdateStatus();
  }

  ngOnDestroy() {
    this.stopUpdatePolling();

    // Clear reboot check interval
    if (this.rebootCheckInterval) {
      clearInterval(this.rebootCheckInterval);
    }
  }

  /**
   * Start checking if device has rebooted and is back online
   */
  private startRebootCheck() {
    // Wait 5 seconds before starting to check (give device time to actually reboot)
    setTimeout(() => {
      let attemptCount = 0;
      const maxAttempts = 60; // Try for 60 seconds

      this.rebootCheckInterval = setInterval(() => {
        attemptCount++;

        // Try to fetch system info
        this.systemService.getInfo().subscribe({
          next: (info) => {
            // Device is back online!
            clearInterval(this.rebootCheckInterval);
            //this.updateStatusMessage = 'Reboot complete, reloading page...';

            // Reload page after a short delay
            setTimeout(() => {
              window.location.reload();
            }, 2000);
          },
          error: (err) => {
            // Device not ready yet, keep trying
            //this.updateStatusMessage = `Reboot in progress... (${attemptCount}/${maxAttempts})`;

            if (attemptCount >= maxAttempts) {
              clearInterval(this.rebootCheckInterval);
              //this.updateStatusMessage = 'The reboot is taking longer than expected. Please refresh manually.';
              this.isOneClickUpdate = false;
            }
          }
        });
      }, 1000); // Check every second
    }, 5000); // Wait 5 seconds before starting
  }

  public onFirmwareFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.selectedFirmwareFile = input.files[0];
    }
  }

  public uploadFirmwareFile() {
    if (this.isOneClickUpdate || this.isFirmwareUploading || this.isWebsiteUploading) {
      return;
    }

    if (!this.selectedFirmwareFile) {
      this.toastrService.warning(this.translate.instant('TOAST.NO_FILE_SELECTED'), this.translate.instant('TOAST.WARNING'));
      return;
    }

    if (this.selectedFirmwareFile.name !== this.expectedFileName) {
      this.toastrService.danger(`${this.translate.instant('TOAST.INCORRECT_FILE')}: ${this.expectedFileName}`, this.translate.instant('TOAST.ERROR'));
      return;
    }

    const file = this.selectedFirmwareFile;
    // Lock all update entry points before opening the OTP dialog. Otherwise a
    // second click can start another upload while authorisation is pending.
    this.isFirmwareUploading = true;

    this.otpAuth.ensureOtp$(
      "",
      this.translate.instant('SECURITY.OTP_TITLE'),
      this.translate.instant('SECURITY.OTP_FW_HINT')
    )
      .pipe(
        switchMap(({ totp }: EnsureOtpResult) => {
          // Persist the second half of the manual update before starting the
          // request. A successful OTA reboot can close HTTP before the browser
          // receives the final response, so response-only persistence is not
          // reliable enough here.
          markManualWwwUpdatePending(localStorage, file.name);
          this.manualWwwUpdatePending = true;
          return this.systemService.performOTAUpdate(file, totp)
            .pipe(this.loadingService.lockUIUntilComplete());
        })
      )
      .subscribe({
        next: (event) => {
          if (event?.type === HttpEventType.UploadProgress && event.total) {
            this.firmwareUpdateProgress = Math.round(100 * event.loaded / event.total);
          } else if (event?.type === HttpEventType.Response) {
            this.firmwareUpdateProgress = 100;
            this.selectedFirmwareFile = null;
            this.toastrService.success(
              this.translate.instant('TOAST.FIRMWARE_UPDATED_WWW_REQUIRED'),
              this.translate.instant('TOAST.SUCCESS'),
              { duration: 10000 },
            );
            this.startRebootCheck();
          }
        },
        error: (err) => {
          this.toastrService.danger(`${this.translate.instant('TOAST.UPLOAD_FAILED')}: ${err.message}`, this.translate.instant('TOAST.ERROR'));
          this.isFirmwareUploading = false;
          this.firmwareUpdateProgress = 0;
        },
        complete: () => {
          this.isFirmwareUploading = false;
          setTimeout(() => this.firmwareUpdateProgress = 0, 500);
        }
      });
  }


  public onWebsiteFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      this.selectedWebsiteFile = input.files[0];
    }
  }

  public async uploadWebsiteFile() {
    if (this.isOneClickUpdate || this.isFirmwareUploading || this.isWebsiteUploading) {
      return;
    }

    if (!this.selectedWebsiteFile) {
      this.toastrService.warning(this.translate.instant('TOAST.NO_FILE_SELECTED'), this.translate.instant('TOAST.WARNING'));
      return;
    }

    const file = this.selectedWebsiteFile;
    // Reading and checking the embedded image identity is asynchronous. Hold
    // the UI lock across validation and OTP authorisation as well as upload.
    this.isWebsiteUploading = true;
    const validation = await validateWebsiteImage(file, this.currentVersion);
    if (!validation.valid) {
      if (validation.error === 'size') {
        this.toastrService.danger(
          this.translate.instant('TOAST.WWW_IMAGE_SIZE'),
          this.translate.instant('TOAST.ERROR'),
        );
      } else if (validation.error === 'version') {
        this.toastrService.danger(
          this.translate.instant('TOAST.WWW_VERSION_MISMATCH', {
            fileVersion: validation.embeddedVersion,
            firmwareVersion: this.currentVersion,
          }),
          this.translate.instant('TOAST.ERROR'),
        );
      } else if (validation.error === 'identity') {
        this.toastrService.danger(
          this.translate.instant('TOAST.WWW_IDENTITY_INVALID'),
          this.translate.instant('TOAST.ERROR'),
        );
      } else {
        this.toastrService.danger(
          `${this.translate.instant('TOAST.INCORRECT_FILE')}: ${this.expectedWebsiteFileName}`,
          this.translate.instant('TOAST.ERROR'),
        );
      }
      this.isWebsiteUploading = false;
      return;
    }

    this.otpAuth.ensureOtp$(
      "",
      this.translate.instant('SECURITY.OTP_TITLE'),
      this.translate.instant('SECURITY.OTP_WWW_HINT')
    )
      .pipe(
        switchMap(({ totp }: EnsureOtpResult) => {
          return this.systemService.performWWWOTAUpdate(file, totp)
            .pipe(this.loadingService.lockUIUntilComplete());
        })
      )
      .subscribe({
        next: (event) => {
          if (!event) return;
          if (event.type === HttpEventType.UploadProgress && event.total) {
            this.websiteUpdateProgress = Math.round(100 * event.loaded / event.total);
          } else if (event.type === HttpEventType.Response) {
            this.websiteUpdateProgress = 100;
            clearPendingManualWwwUpdate(localStorage);
            this.manualWwwUpdatePending = false;
            this.selectedWebsiteFile = null;
            this.toastrService.success(this.translate.instant('TOAST.WEBSITE_UPDATED'), this.translate.instant('TOAST.SUCCESS'));
            setTimeout(() => window.location.reload(), 1000);
          }
        },
        error: (err) => {
          this.toastrService.danger(`${this.translate.instant('TOAST.UPLOAD_FAILED')}: ${err.message}`, this.translate.instant('TOAST.ERROR'));
          this.isWebsiteUploading = false;
          this.websiteUpdateProgress = 0;
        },
        complete: () => {
          this.isWebsiteUploading = false;
          setTimeout(() => this.websiteUpdateProgress = 0, 500);
        }
      });
  }


  /**
   * Update version status based on current and latest versions
   */
  private updateVersionStatus() {
    if (this.currentVersion && this.latestStableRelease) {
      this.updateStatus = this.githubUpdateService.getUpdateStatus(
        this.currentVersion,
        this.latestStableRelease
      );
      this.versionComparison = this.githubUpdateService.getVersionComparison(
        this.currentVersion,
        this.latestStableRelease
      );
    }
    this.updateSelectedReleaseDeps();
  }

  /** Refresh filename + changelog for the selected release */
  private updateSelectedReleaseDeps() {
    if (!this.selectedRelease) {
      this.expectedFactoryFilename = '';
      return;
    }
    this.expectedFactoryFilename = this.buildFactoryNameFor(this.selectedRelease);

    // Refresh changelog if panel is open
    if (this.showChangelog) {
      this.changelog = this.githubUpdateService.getChangelog(this.selectedRelease);
    }
  }


  /**
   * Get status badge color based on update status
   */
  public getStatusBadgeColor(): string {
    switch (this.updateStatus) {
      case UpdateStatus.UP_TO_DATE:
        return 'success';
      case UpdateStatus.UPDATE_AVAILABLE:
        return 'warning';
      case UpdateStatus.OUTDATED:
        return 'danger';
      default:
        return 'basic';
    }
  }

  /**
   * Get translation key for status badge
   * Converts 'up-to-date' to 'UPDATE.STATUS_UP_TO_DATE'
   */
  public getStatusTranslationKey(): string {
    const statusKey = this.updateStatus.toUpperCase().replace(/-/g, '_');
    return `UPDATE.STATUS_${statusKey}`;
  }

  /** Label for dropdown: "vX.Y.Z (latest)" for the newest item */
  public getReleaseLabel(r: GithubRelease, idx: number): string {
    return r.isLatest ? `${r.tag_name} (latest)` : r.tag_name;
  }

  /**
   * Toggle changelog visibility
   */
  public toggleChangelog() {
    this.showChangelog = !this.showChangelog;

    if (this.showChangelog && this.selectedRelease) {
      this.changelog = this.githubUpdateService.getChangelog(this.selectedRelease);
    }
  }

  /**
   * Direct update from GitHub via backend proxy
   */
  public directUpdateFromGithub() {
    if (this.isOneClickUpdate || this.isFirmwareUploading || this.isWebsiteUploading) {
      return;
    }

    if (!this.selectedRelease) {
      this.toastrService.warning(this.translate.instant('TOAST.NO_RELEASE_INFO'), this.translate.instant('TOAST.WARNING'));
      return;
    }

    const filename = this.expectedFactoryFilename;
    console.log('Looking for file:', filename);
    console.log('Device model:', this.deviceModel);
    //console.log('Available assets:', this.selectedRelease?.assets?.map(a => a.name) ?? []);
    const asset = this.githubUpdateService.findAsset(this.selectedRelease, filename);
    if (!asset) {
      this.toastrService.danger(`File "${filename}" not found.`, 'Error', { duration: 10000 });
      return;
    }

    const assetUrl = asset.browser_download_url;
    let updateAccepted = false;

    // Reserve the UI update state before OTP authorisation so another update
    // route cannot be entered while the dialog is open.
    this.isOneClickUpdate = true;
    this.otaProgress = 0;
    this.firmwareUpdateProgress = 0;

    this.otpAuth.ensureOtp$(
      "",
      this.translate.instant('SECURITY.OTP_TITLE'),
      this.translate.instant('SECURITY.OTP_FW_HINT')
    )
      .pipe(
        switchMap(({ totp }: EnsureOtpResult) => {
          // kick the backend update
          const keepConfig = this.keepConfigCtrl.value ?? true;
          return this.systemService.performGithubOTAUpdate(assetUrl, keepConfig, totp);
        })
      )
      .subscribe({
        next: () => {
          updateAccepted = true;
          this.startUpdatePolling();
        },
        error: (err) => {
          this.toastrService.danger(`${this.translate.instant('TOAST.UPDATE_FAILED')}: ${err.message || err.error}`, this.translate.instant('TOAST.ERROR'));
          this.isOneClickUpdate = false;
        },
        complete: () => {
          // OTP cancellation completes without starting an HTTP request.
          if (!updateAccepted) {
            this.isOneClickUpdate = false;
          }
        }
      });
  }

  private startUpdatePolling() {
    this.stopUpdatePolling();
    this.sawRebooting = false;
    this.consecutiveStatusErrors = 0;
    this.otaErrorShown = false;

    this.updateStatusSub = interval(1000)
      .pipe(
        // Poll OTA status every second
        switchMap(() => this.systemService.getGithubOTAStatus().pipe(
          catchError((err) => {
            this.consecutiveStatusErrors++;

            if (this.sawRebooting) {
              // Losing HTTP after the backend announced `rebooting` is expected;
              // startRebootCheck() owns recovery from this point onward.
              setTimeout(() => this.stopUpdatePolling());
            } else if (this.consecutiveStatusErrors >= 5) {
              this.failOneClickUpdate(err?.message || err?.error || 'status unavailable');
            }
            // Keep the outer interval alive for transient network failures.
            return of(null);
          })
        )),
        tap((status: IUpdateStatus | null) => {
          if (!status) {
            return;
          }
          this.consecutiveStatusErrors = 0;

          // Update UI state
          this.otaProgress = status.progress;
          this.currentStep = `UPDATE.STEP_${status.step.toUpperCase()}`;

          if (status.step === 'error') {
            this.failOneClickUpdate();
            return;
          }

          // check if device finished updating and only fire the success toast a single time
          if (status.step === 'rebooting' && !this.sawRebooting) {
            this.sawRebooting = true;
            // The one-click factory image contains firmware and WWW together,
            // so it also completes any stale manual-update reminder.
            clearPendingManualWwwUpdate(localStorage);
            this.manualWwwUpdatePending = false;
            this.toastrService.success(this.translate.instant('TOAST.FIRMWARE_UPDATED'), this.translate.instant('TOAST.SUCCESS'));
            this.startRebootCheck();
          }
        })
      )
      .subscribe({
        error: (err) => {
          this.failOneClickUpdate(err?.message || err?.error);
        }
      });
  }

  private failOneClickUpdate(detail?: string) {
    this.isOneClickUpdate = false;
    this.stopUpdatePolling();
    if (!this.otaErrorShown) {
      const message = detail
        ? `${this.translate.instant('TOAST.UPDATE_FAILED')}: ${detail}`
        : this.translate.instant('TOAST.UPDATE_FAILED');
      this.toastrService.danger(message, this.translate.instant('TOAST.ERROR'));
      this.otaErrorShown = true;
    }
  }

  // we can resume the update progress status on a page reload because
  // the OTA update is not done in HTTP server context anymore! 😍
  private checkUpdateStatus() {
    // Single-shot status fetch
    this.systemService.getGithubOTAStatus()
      .pipe(take(1))
      .subscribe({
        next: (status: IUpdateStatus) => {
          // If update is ongoing, (re)start polling
          if (status.pending || status.running) {
            this.isOneClickUpdate = true;
            this.otaProgress = status.progress;
            this.currentStep = `UPDATE.STEP_${status.step.toUpperCase()}`;
            this.startUpdatePolling();
          } else if (status.step === 'error') {
            this.otaProgress = status.progress;
            this.currentStep = `UPDATE.STEP_${status.step.toUpperCase()}`;
            this.failOneClickUpdate();
          }
        },
      });
  }

  private stopUpdatePolling() {
    if (this.updateStatusSub) {
      this.updateStatusSub.unsubscribe();
      this.updateStatusSub = undefined;
    }
  }

  /**
   * Get filtered assets (only matching factory firmware)
   */
  public getFilteredAssets(): any[] {
    return this.latestStableRelease?.assets?.filter(asset =>
      asset.name === this.expectedFactoryFilename
    ) ?? [];
  }

  // settings.component.ts
  public onSelectReleaseId(id: number) {
    this.releases$.pipe(take(1)).subscribe(list => {
      const sel = list.find(r => r.id === id);
      if (sel) {
        this.selectedRelease = sel;
        this.updateSelectedReleaseDeps();
      }
    });
  }

  // settings.component.ts
  public trackRelease = (_: number, r: GithubRelease) => r.id;

  // Helper to build expected factory filename for a given release
  private buildFactoryNameFor(release: GithubRelease): string {
    return this.githubUpdateService.findFactoryAsset(
      release,
      this.deviceModel,
      this.currentVersion
    )?.name ?? this.githubUpdateService.getDefaultFactoryFilename(
      release,
      this.deviceModel,
      this.currentVersion
    );
  }

  public getAppVersion() {
    return getAppVersion();
  }

  public get hasVersionMismatch(): boolean {
    return this.currentWebVersion !== '' && this.currentVersion !== this.currentWebVersion;
  }

  public get websiteUpdateRequired(): boolean {
    return this.manualWwwUpdatePending || this.hasVersionMismatch;
  }

  public get expectedWebsiteFileName(): string {
    return this.currentVersion
      ? `www.bin / www-NerdQAxePlus-LTS-${this.currentVersion}.bin / www-${this.currentVersion}.bin`
      : 'www.bin';
  }

  private syncManualWwwUpdateState(): void {
    if (this.currentWebVersion && !this.hasVersionMismatch) {
      clearPendingManualWwwUpdate(localStorage);
    }

    this.manualWwwUpdatePending = this.hasVersionMismatch || hasPendingManualWwwUpdate(localStorage);
  }

}
