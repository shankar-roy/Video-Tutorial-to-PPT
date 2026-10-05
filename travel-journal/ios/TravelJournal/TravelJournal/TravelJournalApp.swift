import SwiftData
import SwiftUI

@main
struct TravelJournalApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
        }
        .modelContainer(for: [CachedHome.self, CachedStayRecord.self, CachedTripRecord.self])
    }
}

struct RootView: View {
    @Environment(\.modelContext) private var context
    @EnvironmentObject private var model: AppModel

    var body: some View {
        TabView {
            StaysView()
                .tabItem { Label("Stays", systemImage: "map") }
            TripListView()
                .tabItem { Label("Journal", systemImage: "book") }
            AccountView()
                .tabItem { Label("Account", systemImage: "person") }
        }
        .task { model.restore(context: context) }
    }
}
