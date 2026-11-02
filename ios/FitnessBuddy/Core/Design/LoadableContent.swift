import FitnessCore
import SwiftUI

/// Why a screen (or a part of it) has nothing to show yet.
enum LoadState: Equatable {
    /// Waiting for the first answer; the text names what loads ("lifting stats").
    case loading(String? = nil)
    /// No backend configured; the text says what to do.
    case notConfigured(String)
    /// The server could not be reached and nothing is cached.
    case offline(String)
    /// The server answered with an error.
    case failed(String)
    /// Loaded (or tried), and there is nothing.
    case empty(title: String, symbol: String, message: String)
}

/// The one placeholder for loading / empty / error / offline, in the three looks the app uses:
/// - `.card`: a card inside a scrolling page (stats screens),
/// - `.notice`: a card with a coloured header (the Coach tab's status notice),
/// - `.screen`: the system's centred `ContentUnavailableView` / spinner for whole screens and list overlays.
///
/// `LoadableContent(state) { content }` shows the content when `state` is nil; `LoadableContent(value, else: state)
/// { value in ... }` when a value is there; `LoadableContent(state)` alone is just the placeholder.
struct LoadableContent<Content: View>: View {
    enum Style { case card, notice, screen }

    private let state: LoadState?
    private let style: Style
    private let retry: (() -> Void)?
    private let content: () -> Content

    init(_ state: LoadState?, style: Style = .card, retry: (() -> Void)? = nil, @ViewBuilder content: @escaping () -> Content) {
        self.state = state
        self.style = style
        self.retry = retry
        self.content = content
    }

    /// The content for a loaded value; the placeholder for `state` while there is none.
    init<Value>(_ value: Value?, else state: @autoclosure () -> LoadState, style: Style = .card, retry: (() -> Void)? = nil,
                @ViewBuilder content: @escaping (Value) -> Content) {
        self.state = value == nil ? state() : nil
        self.style = style
        self.retry = retry
        self.content = { content(value!) }
    }

    var body: some View {
        if let state {
            switch style {
            case .card: card(state)
            case .notice: notice(state)
            case .screen: screen(state)
            }
        } else {
            content()
        }
    }

    // MARK: Card (stats pages)

    @ViewBuilder
    private func card(_ state: LoadState) -> some View {
        Card {
            switch state {
            case .loading(let what):
                HStack(spacing: 10) {
                    ProgressView()
                    Text(what.map { "Loading \($0.lowercased())…" } ?? "Loading…").font(.subheadline).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 80)
            case .notConfigured(let text):
                cardText("Connect your backend", symbol: "icloud.and.arrow.down", text)
            case .offline(let text):
                cardText("Offline", symbol: "icloud.slash", text)
            case .failed(let text):
                cardText("Couldn't load", symbol: "exclamationmark.triangle", text)
            case .empty(let title, let symbol, let text):
                cardText(title, symbol: symbol, text)
            }
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func cardText(_ title: String, symbol: String, _ text: String) -> some View {
        Label(title, systemImage: symbol).font(.headline)
        Text(text).font(.subheadline).foregroundStyle(.secondary)
    }

    // MARK: Notice (coach)

    @ViewBuilder
    private func notice(_ state: LoadState) -> some View {
        switch state {
        case .loading:
            Card {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text("Loading…").foregroundStyle(.secondary) }
                    .font(.subheadline)
            }
        case .notConfigured(let text): noticeCard("Not Set Up", symbol: "link", text)
        case .offline(let text): noticeCard("Offline", symbol: "icloud.slash", text)
        case .failed(let text): noticeCard("Couldn't Load", symbol: "exclamationmark.triangle", text)
        case .empty(let title, let symbol, let text): noticeCard(title, symbol: symbol, text)
        }
    }

    private func noticeCard(_ title: String, symbol: String, _ text: String) -> some View {
        Card {
            CardHeader(title: title, symbol: symbol, color: .secondary)
            Text(text).font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .accessibilityElement(children: .combine)
    }

    // MARK: Screen (whole screens, list overlays)

    @ViewBuilder
    private func screen(_ state: LoadState) -> some View {
        switch state {
        case .loading:
            ProgressView()
        case .notConfigured(let text):
            unavailable("Connect your backend", symbol: "icloud.and.arrow.down", text)
        case .offline(let text):
            unavailable("Offline", symbol: "icloud.slash", text)
        case .failed(let text):
            unavailable("Couldn't load", symbol: "exclamationmark.triangle", text)
        case .empty(let title, let symbol, let text):
            unavailable(title, symbol: symbol, text)
        }
    }

    @ViewBuilder
    private func unavailable(_ title: String, symbol: String, _ text: String) -> some View {
        if let retry {
            ContentUnavailableView {
                Label(title, systemImage: symbol)
            } description: {
                Text(text)
            } actions: {
                Button("Try again", action: retry)
            }
        } else {
            ContentUnavailableView(title, systemImage: symbol, description: Text(text))
        }
    }
}

extension LoadableContent where Content == EmptyView {
    /// Just the placeholder (for screens that lay out the rest themselves, and list overlays).
    init(_ state: LoadState, style: Style = .card, retry: (() -> Void)? = nil) {
        self.init(state, style: style, retry: retry) { EmptyView() }
    }
}

extension StatsResource {
    /// The placeholder while nothing is cached: not configured, the error (offline or not), or loading `what`.
    func loadState(_ what: String) -> LoadState {
        if !isConfigured {
            return .notConfigured("Add the backend URL and API token in Settings (Today tab). Stats are cached afterwards and stay available offline.")
        }
        if let failure {
            return isOffline ? .offline("Nothing cached yet. Connect once and this screen stays available offline.") : .failed(failure)
        }
        return .loading(what)
    }
}
