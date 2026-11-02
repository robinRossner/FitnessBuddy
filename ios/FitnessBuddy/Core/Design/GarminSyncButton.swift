import FitnessCore
import SwiftUI

/// Toolbar "Sync" button (next to `SyncStatusBadge`): the backend pulls Garmin now, then the app syncs and reloads
/// `resources` (the stats the screen shows, as for pull-to-refresh). Logic lives in `GarminSyncController`.
struct GarminSyncButton: View {
    @Environment(AppEnvironment.self) private var env
    let resources: [any StatsLoadable]

    var body: some View {
        let garmin = env.garmin
        Button {
            Task { await garmin.sync(refreshing: resources) }
        } label: {
            if garmin.isRunning {
                ProgressView().controlSize(.small)
            } else {
                Image(systemName: "arrow.triangle.2.circlepath")
            }
        }
        .disabled(garmin.isRunning)
        .accessibilityLabel(garmin.isRunning ? "Syncing with Garmin" : "Sync with Garmin")
        .accessibilityHint("Fetches your latest activities from Garmin Connect")
    }
}

/// The short result banner of the Sync button, above the tab bar. Dismisses itself.
private struct GarminSyncBanner: ViewModifier {
    @Environment(AppEnvironment.self) private var env

    func body(content: Content) -> some View {
        let garmin = env.garmin
        content.safeAreaInset(edge: .bottom, spacing: 0) {
            if let feedback = garmin.feedback {
                GarminSyncBannerView(feedback: feedback,
                                     openConnect: { Task { await garmin.openGarminConnect() } },
                                     dismiss: { garmin.dismissFeedback() })
                    .padding(.horizontal, 16).padding(.bottom, 8)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                    .task(id: garmin.feedbackId) {
                        try? await Task.sleep(for: .seconds(feedback.offersConnect ? 15 : 5))
                        if !Task.isCancelled { garmin.dismissFeedback(id: garmin.feedbackId) }
                    }
            }
        }
        .animation(.easeInOut(duration: 0.2), value: garmin.feedbackId)
    }
}

private struct GarminSyncBannerView: View {
    let feedback: GarminSyncFeedback
    let openConnect: () -> Void
    let dismiss: () -> Void

    private var tint: Color {
        switch feedback.tone {
        case .success: Domain.running.color
        case .neutral: .secondary
        case .warning: .orange
        }
    }

    private var symbol: String {
        switch feedback.tone {
        case .success: "checkmark.circle.fill"
        case .neutral: "clock.fill"
        case .warning: "exclamationmark.triangle.fill"
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol).font(.title3).foregroundStyle(tint).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(feedback.title).font(.subheadline.weight(.semibold))
                if let detail = feedback.detail {
                    Text(detail).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                if feedback.offersConnect {
                    Button("Open Garmin Connect", action: openConnect)
                        .font(.subheadline.weight(.semibold))
                        .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button(action: dismiss) {
                Image(systemName: "xmark").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Dismiss")
        }
        .padding(14)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: Kit.cardRadius, style: .continuous))
        .shadow(color: .black.opacity(0.12), radius: 10, y: 3)
        .accessibilityElement(children: .contain)
    }
}

extension View {
    /// Shows the Sync button's result banner at the bottom of this screen.
    func garminSyncBanner() -> some View { modifier(GarminSyncBanner()) }
}
