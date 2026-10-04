// Desktop / Notification Center widget showing the latest techdash brief.
//
// Reads http://127.0.0.1:8787/api/brief. The widget is sandboxed and can't
// launch anything, so its buttons are techdash:// links that the host app
// (the menu bar app) handles: open starts the server, write runs /brief.

import SwiftUI
import WidgetKit

struct BriefLink: Decodable { let src: String?; let url: String? }
struct BriefItem: Decodable, Identifiable {
    let title: String
    let tag: String?
    let links: [BriefLink]?
    var id: String { title }
    var firstURL: URL? { links?.lazy.compactMap { $0.url.flatMap(URL.init(string:)) }.first }
}
struct Brief: Decodable {
    let headline: String?
    let items: [BriefItem]?
    let built_at: Double?
}

struct BriefEntry: TimelineEntry {
    let date: Date
    let brief: Brief?
    let offline: Bool
}

let openURL = URL(string: "techdash://open")!
let writeURL = URL(string: "techdash://write")!

struct Provider: TimelineProvider {
    func placeholder(in context: Context) -> BriefEntry {
        BriefEntry(date: .now, brief: Brief(
            headline: "A quiet day: one release worth installing, one worth reading about.",
            items: [BriefItem(title: "Claude Code ships a new version", tag: "claude", links: nil),
                    BriefItem(title: "Ollama runs on MLX by default", tag: "ai", links: nil)],
            built_at: nil), offline: false)
    }

    func getSnapshot(in context: Context, completion: @escaping (BriefEntry) -> Void) {
        load { completion($0 ?? placeholder(in: context)) }
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<BriefEntry>) -> Void) {
        load { entry in
            let entry = entry ?? BriefEntry(date: .now, brief: nil, offline: true)
            // Offline: check back sooner, the app may have started the server.
            let next = Date.now.addingTimeInterval(entry.offline ? 120 : 900)
            completion(Timeline(entries: [entry], policy: .after(next)))
        }
    }

    func load(_ done: @escaping (BriefEntry?) -> Void) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8787/api/brief")!)
        request.timeoutInterval = 4
        URLSession.shared.dataTask(with: request) { data, response, _ in
            guard let status = (response as? HTTPURLResponse)?.statusCode else { return done(nil) }
            let brief = status == 200 ? data.flatMap { try? JSONDecoder().decode(Brief.self, from: $0) } : nil
            done(BriefEntry(date: .now, brief: brief, offline: false))
        }.resume()
    }
}

let tagColors: [String: Color] = [
    "claude": .orange, "agents": .pink, "ai": .purple, "mcp": .indigo,
    "go": .cyan, "react": .blue, "py": .yellow, "js": .yellow, "rust": .brown,
    "infra": .teal, "market": .green, "news": .gray, "eng": .mint, "web": .blue,
]

struct TagChip: View {
    let tag: String
    var body: some View {
        Text(tag.uppercased())
            .font(.system(size: 8, weight: .bold))
            .padding(.horizontal, 4).padding(.vertical, 1.5)
            .background((tagColors[tag] ?? .gray).opacity(0.25), in: RoundedRectangle(cornerRadius: 3))
            .foregroundStyle(tagColors[tag] ?? .gray)
    }
}

struct TechdashWidgetView: View {
    @Environment(\.widgetFamily) var family
    let entry: BriefEntry

    var maxItems: Int {
        switch family {
        case .systemExtraLarge: 10
        case .systemLarge: 7
        case .systemMedium: 3
        default: 0
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header
            if let brief = entry.brief, let headline = brief.headline {
                Text(headline)
                    .font(.system(size: family == .systemSmall ? 12 : 13, weight: .semibold))
                    .lineLimit(family == .systemSmall ? 6 : 3)
                if maxItems > 0 {
                    Divider().opacity(0.4)
                    ForEach((brief.items ?? []).prefix(maxItems)) { item in
                        row(item)
                    }
                }
                Spacer(minLength: 0)
                if family != .systemSmall { footer }
            } else {
                Spacer(minLength: 0)
                Text(entry.offline ? "techdash isn't running." : "No brief yet.")
                    .font(.system(size: 13, weight: .semibold))
                Text(entry.offline ? "Click to start it." : "Write one to see it here.")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 0)
                if family != .systemSmall { footer }
            }
        }
        .widgetURL(openURL)
    }

    var header: some View {
        HStack(spacing: 5) {
            Image(systemName: "newspaper.fill").foregroundStyle(.orange)
            Text("TECH BRIEF").font(.system(size: 10, weight: .bold)).foregroundStyle(.secondary)
            Spacer()
            if let built = entry.brief?.built_at {
                Text(Date(timeIntervalSince1970: built), style: .relative)
                    .font(.system(size: 10)).foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
            }
        }
    }

    @ViewBuilder
    func row(_ item: BriefItem) -> some View {
        let content = HStack(alignment: .firstTextBaseline, spacing: 6) {
            TagChip(tag: item.tag ?? "news").frame(width: 46, alignment: .leading)
            Text(item.title).font(.system(size: 11.5)).lineLimit(family == .systemExtraLarge ? 2 : 1)
        }
        if let url = item.firstURL { Link(destination: url) { content } } else { content }
    }

    var footer: some View {
        HStack(spacing: 8) {
            Link(destination: openURL) {
                Label("Dashboard", systemImage: "rectangle.grid.2x2")
            }
            Link(destination: writeURL) {
                Label("New brief", systemImage: "sparkles")
            }
            Spacer()
        }
        .font(.system(size: 10, weight: .medium))
        .foregroundStyle(.secondary)
    }
}

@main
struct TechdashWidget: Widget {
    var body: some WidgetConfiguration {
        StaticConfiguration(kind: "TechdashBrief", provider: Provider()) { entry in
            TechdashWidgetView(entry: entry)
                .containerBackground(.background, for: .widget)
        }
        .configurationDisplayName("Tech Brief")
        .description("Today's techdash brief. Click to open the dashboard.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge, .systemExtraLarge])
    }
}
