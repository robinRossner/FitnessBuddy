import FitnessCore
import Foundation
import Observation
import SwiftData

/// Keeps the local cache tied to one backend: wipes it when the backend (or token) changes, on "Reset local data",
/// and when demo data is cleared. Synchronous wipes, then (where the user asked for it) a full sync.
@MainActor
@Observable
final class BackendResetController {
    @ObservationIgnored private let context: ModelContext
    @ObservationIgnored private let settings: SettingsStore
    @ObservationIgnored private let sync: SyncCoordinator
    @ObservationIgnored private let coach: CoachRepository
    @ObservationIgnored private let gyms: GymRepository
    /// Clears app-level state that points into the wiped data (the pending Coach route; set by the owner).
    @ObservationIgnored var didWipe: @MainActor () -> Void = {}

    init(context: ModelContext, settings: SettingsStore, sync: SyncCoordinator, coach: CoachRepository, gyms: GymRepository) {
        self.context = context
        self.settings = settings
        self.sync = sync
        self.coach = coach
        self.gyms = gyms
    }

    /// Wipes the local cache if it belongs to a different backend (or to demo data). Synchronous, no network.
    @discardableResult
    func reconcileBackend() -> Bool {
        let fp = settings.fingerprint
        let state = SyncState.current(in: context)
        // Nothing configured and no demo/old cache to drop: keep whatever the user logged offline.
        if fp == nil && !state.demoActive && state.backendFingerprint == nil { return false }
        // Demo data without a configured backend stays (it is the demo).
        if fp == nil && state.demoActive { return false }
        guard BackendReset.needsReset(fingerprint: fp, in: context) else { return false }
        wipeLocalData(fingerprint: fp)
        return true
    }

    /// True when applying this backend would wipe the cache.
    func backendChangeWipes(url: String, token: String) -> Bool {
        let fp = BackendReset.fingerprint(url: url, token: token)
        let state = SyncState.current(in: context)
        if fp == nil && state.demoActive { return false }
        return BackendReset.needsReset(fingerprint: fp, in: context)
    }

    var unsyncedRecordCount: Int { BackendReset.unsyncedCount(in: context) }

    private func wipeLocalData(fingerprint: String?) {
        BackendReset.reset(in: context, fingerprint: fingerprint)
        coach.reset()
        gyms.reset()
        didWipe()
        settings.currentGymId = nil
        sync.didResetData()
    }

    /// DEBUG demo mode off again: drops demo rows and caches, keeps real unsynced records.
    func clearDemoData() {
        wipeLocalData(fingerprint: SyncState.current(in: context).backendFingerprint)
    }

    /// Settings > Save: stores URL + token, wipes the cache when the backend (or the token) changed, then runs a
    /// full sync right away. Returns false if the token could not be written to the Keychain.
    @discardableResult
    func saveBackend(url: String, token: String) async -> Bool {
        await sync.cancelAndWait()
        let stored = settings.update(url: url, token: token)
        if backendChangeWipes(url: settings.backendURL, token: settings.apiToken) { wipeLocalData(fingerprint: settings.fingerprint) }
        await sync.sync(.settings)
        return stored
    }

    /// Settings > Reset local data & resync: same wipe, same fingerprint, then a full sync.
    func resetLocalDataAndResync() async {
        await sync.cancelAndWait()
        wipeLocalData(fingerprint: settings.fingerprint)
        await sync.sync(.manual)
    }
}
