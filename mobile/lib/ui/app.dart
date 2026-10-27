import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import 'screens/contacts_screen.dart';
import 'screens/home_screen.dart';
import 'screens/jobs_screen.dart';
import 'screens/runs_screen.dart';
import 'screens/settings_screen.dart';
import 'screens/tool_run_screen.dart';
import 'screens/tools_screen.dart';

GoRouter buildRouter({String initialLocation = '/'}) => GoRouter(
      initialLocation: initialLocation,
      routes: [
        StatefulShellRoute.indexedStack(
          builder: (context, state, shell) => _Shell(shell: shell),
          branches: [
            StatefulShellBranch(routes: [GoRoute(path: '/', builder: (_, _) => const HomeScreen())]),
            StatefulShellBranch(routes: [GoRoute(path: '/jobs', builder: (_, _) => const JobsScreen())]),
            StatefulShellBranch(routes: [GoRoute(path: '/contacts', builder: (_, _) => const ContactsScreen())]),
            StatefulShellBranch(routes: [GoRoute(path: '/runs', builder: (_, _) => const RunsScreen())]),
            StatefulShellBranch(routes: [GoRoute(path: '/tools', builder: (_, _) => const ToolsScreen())]),
            StatefulShellBranch(routes: [
              GoRoute(
                path: '/settings',
                builder: (_, _) => const SettingsScreen(),
                routes: [GoRoute(path: 'profile', builder: (_, _) => const ProfileEditorScreen())],
              ),
            ]),
          ],
        ),
        GoRoute(
          path: '/tools/:name',
          builder: (_, s) => ToolRunScreen(
            name: s.pathParameters['name']!,
            initial: (s.extra as Map?)?.cast<String, dynamic>() ?? const {},
          ),
        ),
        GoRoute(path: '/runs/:id', builder: (_, s) => RunDetailScreen(runId: s.pathParameters['id']!)),
      ],
    );

class _Shell extends StatelessWidget {
  const _Shell({required this.shell});
  final StatefulNavigationShell shell;

  static const _dest = [
    NavigationDestination(icon: Icon(Icons.dashboard_outlined), selectedIcon: Icon(Icons.dashboard), label: 'Home'),
    NavigationDestination(icon: Icon(Icons.work_outline), selectedIcon: Icon(Icons.work), label: 'Jobs'),
    NavigationDestination(icon: Icon(Icons.contacts_outlined), selectedIcon: Icon(Icons.contacts), label: 'Contacts'),
    NavigationDestination(icon: Icon(Icons.terminal_outlined), selectedIcon: Icon(Icons.terminal), label: 'Runs'),
    NavigationDestination(icon: Icon(Icons.apps_outlined), selectedIcon: Icon(Icons.apps), label: 'Tools'),
    NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: shell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: shell.currentIndex,
        labelBehavior: NavigationDestinationLabelBehavior.onlyShowSelected,
        destinations: _dest,
        onDestinationSelected: (i) => shell.goBranch(i, initialLocation: i == shell.currentIndex),
      ),
    );
  }
}

class JobSeekerApp extends ConsumerStatefulWidget {
  const JobSeekerApp({super.key, this.router});
  final GoRouter? router;

  @override
  ConsumerState<JobSeekerApp> createState() => _JobSeekerAppState();
}

class _JobSeekerAppState extends ConsumerState<JobSeekerApp> {
  late final GoRouter _router = widget.router ?? buildRouter();

  static ThemeData _theme(Brightness b) {
    final scheme = ColorScheme.fromSeed(seedColor: const Color(0xFF3F51B5), brightness: b);
    return ThemeData(
      colorScheme: scheme,
      useMaterial3: true,
      inputDecorationTheme: const InputDecorationTheme(border: OutlineInputBorder()),
      cardTheme: const CardThemeData(margin: EdgeInsets.zero),
    );
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp.router(
      title: 'Agentic Job Seeker',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      routerConfig: _router,
    );
  }
}
