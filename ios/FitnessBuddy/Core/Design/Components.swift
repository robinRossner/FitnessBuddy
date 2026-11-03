import SwiftUI

extension View {
    /// Marks a tab's top-level view for the UI tests (`AXID.Screen`). A container element of its own, so the identifier is
    /// found once and is not copied onto every child.
    func screenIdentifier(_ id: String) -> some View {
        accessibilityElement(children: .contain).accessibilityIdentifier(id)
    }
}

/// The one card of the app (Apple Health style): rounded, on the grouped background. See DesignKit.swift.
/// `tint` draws a thin coloured bar on the leading edge (older screens); new screens use a `CardHeader` instead.
struct Card<Content: View>: View {
    var tint: Color? = nil
    var padding: CGFloat = 16
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.cardBackground, in: RoundedRectangle(cornerRadius: Kit.cardRadius, style: .continuous))
            .overlay(alignment: .leading) {
                if let tint {
                    Capsule().fill(tint).frame(width: 4).padding(.vertical, 14).padding(.leading, 5)
                        .accessibilityHidden(true)
                }
            }
            .contentShape(Rectangle())
    }
}

struct SectionLabel: View {
    let title: String
    var symbol: String? = nil
    var color: Color = .secondary

    var body: some View {
        Label {
            Text(title).textCase(.uppercase)
        } icon: {
            if let symbol { Image(systemName: symbol) }
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(color)
        .labelStyle(.titleAndIcon)
    }
}

/// Fitness-style ring. `progress` 0...1; with `dashed` it draws a faint dashed outline (unknown state).
struct Ring<Center: View>: View {
    var progress: Double = 1
    var color: Color
    var lineWidth: CGFloat = 10
    var dashed = false
    @ViewBuilder var center: Center

    var body: some View {
        ZStack {
            Circle().stroke(color.opacity(0.18), style: StrokeStyle(lineWidth: lineWidth, dash: dashed ? [3, 7] : []))
            if !dashed {
                Circle()
                    .trim(from: 0, to: max(0.001, min(1, progress)))
                    .stroke(color.gradient, style: StrokeStyle(lineWidth: lineWidth, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            center
        }
        .padding(lineWidth / 2)
    }
}

struct StatTile: View {
    let title: String
    let value: String
    var unit: String? = nil
    var color: Color = .primary

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value).metricStyle(.title2).foregroundStyle(color)
                if let unit { Text(unit).font(.footnote.weight(.medium)).foregroundStyle(.secondary) }
            }
            .minimumScaleFactor(0.7).lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

/// Small coloured capsule: "PR", "+2.5 kg", "Deload", "Today".
struct Chip: View {
    let text: String
    var color: Color = .secondary
    var symbol: String? = nil

    var body: some View {
        HStack(spacing: 3) {
            if let symbol { Image(systemName: symbol).accessibilityHidden(true) }
            Text(text)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(color)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .lineLimit(1)
        .background(color.opacity(0.14), in: Capsule())
    }
}

struct ComingSoonView: View {
    let title: String
    let symbol: String
    let message: String

    var body: some View {
        NavigationStack {
            ContentUnavailableView {
                Label(title, systemImage: symbol)
            } description: {
                Text(message)
            }
            .navigationTitle(title)
        }
    }
}
