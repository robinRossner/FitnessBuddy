import Foundation
import Network
import Observation

@MainActor
@Observable
final class NetworkMonitor {
    private(set) var isConnected = true
    /// Called when connectivity flips from unsatisfied to satisfied.
    @ObservationIgnored var onBecameConnected: (() -> Void)?

    @ObservationIgnored private let monitor = NWPathMonitor()
    @ObservationIgnored private let queue = DispatchQueue(label: "NetworkMonitor")
    @ObservationIgnored private var started = false

    func start() {
        guard !started else { return }
        started = true
        monitor.pathUpdateHandler = { [weak self] path in
            let ok = path.status == .satisfied
            Task { @MainActor [weak self] in self?.update(connected: ok) }
        }
        monitor.start(queue: queue)
    }

    private func update(connected: Bool) {
        let was = isConnected
        isConnected = connected
        if connected && !was { onBecameConnected?() }
    }
}
