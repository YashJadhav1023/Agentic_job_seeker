import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../state/providers.dart';
import '../widgets/common.dart';
import 'tool_run_screen.dart';

/// Dashboard: readiness, today's counters vs caps, kill switch, and one-tap shortcuts.
class HomeScreen extends ConsumerWidget {
  const HomeScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(statusProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Agentic Job Seeker'), actions: [
        IconButton(tooltip: 'Refresh', icon: const Icon(Icons.refresh), onPressed: () => ref.invalidate(statusProvider)),
      ]),
      body: AsyncBody<Map<String, dynamic>>(
        value: status,
        onRetry: () {
          ref.invalidate(statusProvider);
          if (ref.read(clientProvider) == null) context.go('/settings');
        },
        data: (s) => RefreshIndicator(
          onRefresh: () => ref.refresh(statusProvider.future),
          child: ListView(padding: const EdgeInsets.all(16), children: [
            _Header(s),
            const SizedBox(height: 12),
            _Today(s),
            const SizedBox(height: 12),
            _Sessions(s),
            const SizedBox(height: 12),
            _KillSwitch(s),
            const SizedBox(height: 20),
            Text('Quick actions', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            const _QuickActions(),
          ]),
        ),
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context) {
    final live = s['real_actions_allowed'] == true;
    final missing = (s['missing_profile_fields'] as List?) ?? const [];
    final cs = Theme.of(context).colorScheme;
    return Card(
      color: cs.primaryContainer,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Hi ${s['candidate'] ?? ''}', style: Theme.of(context).textTheme.headlineSmall),
          const SizedBox(height: 4),
          Text('Searching in ${((s['locations'] as List?) ?? const []).join(', ')}'),
          const SizedBox(height: 12),
          Row(children: [
            Icon(live ? Icons.verified_user : Icons.shield_outlined),
            const SizedBox(width: 8),
            Expanded(
              child: Text(live
                  ? 'Live actions enabled (dry-run default: ${s['dry_run_default']})'
                  : 'Safe mode: ${s['real_actions_blocked_reason'] ?? 'dry-run only'}'),
            ),
          ]),
          if (missing.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('Missing in profile: ${missing.join(', ')}', style: TextStyle(color: cs.error)),
          ],
        ]),
      ),
    );
  }
}

class _Today extends StatelessWidget {
  const _Today(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context) {
    final t = (s['today'] as Map?) ?? const {};
    final caps = (t['caps'] as Map?) ?? const {};
    Widget meter(String label, IconData icon, num v, num cap) => Expanded(
          child: Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Row(children: [Icon(icon, size: 18), const SizedBox(width: 6), Text(label)]),
                const SizedBox(height: 8),
                Text('$v / $cap', style: Theme.of(context).textTheme.headlineSmall),
                const SizedBox(height: 8),
                LinearProgressIndicator(value: cap == 0 ? 0 : (v / cap).clamp(0, 1).toDouble()),
              ]),
            ),
          ),
        );
    return Row(children: [
      meter('Applied today', Icons.send_outlined, (t['applications'] as num?) ?? 0, (caps['applications'] as num?) ?? 0),
      meter('Emails today', Icons.mail_outline, (t['emails_sent'] as num?) ?? 0, (caps['emails'] as num?) ?? 0),
    ]);
  }
}

class _Sessions extends ConsumerWidget {
  const _Sessions(this.s);
  final Map<String, dynamic> s;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sessions = ((s['login_sessions_saved'] as Map?) ?? const {}).cast<String, dynamic>();
    final creds = ((s['credentials'] as Map?) ?? const {}).cast<String, dynamic>();
    return Card(
      child: Column(children: [
        const ListTile(title: Text('Accounts'), dense: true),
        for (final e in sessions.entries)
          ListTile(
            leading: Icon(e.value == true ? Icons.check_circle : Icons.login,
                color: e.value == true ? Colors.green : Theme.of(context).colorScheme.outline),
            title: Text(e.key[0].toUpperCase() + e.key.substring(1)),
            subtitle: Text(e.value == true ? 'Logged in' : 'Not logged in'),
            trailing: e.value == true
                ? null
                : TextButton(
                    onPressed: () => context.push('/tools/login_platform', extra: {'platform': e.key}),
                    child: const Text('Log in'),
                  ),
          ),
        ListTile(
          leading: Icon(creds['gmail_app_password'] == true ? Icons.check_circle : Icons.key_off,
              color: creds['gmail_app_password'] == true ? Colors.green : Theme.of(context).colorScheme.outline),
          title: const Text('Gmail sending'),
          subtitle: Text(creds['gmail_app_password'] == true ? 'App password saved' : 'Add app password in Settings'),
          trailing: creds['gmail_app_password'] == true
              ? null
              : TextButton(onPressed: () => context.go('/settings'), child: const Text('Set up')),
        ),
      ]),
    );
  }
}

class _KillSwitch extends ConsumerStatefulWidget {
  const _KillSwitch(this.s);
  final Map<String, dynamic> s;

  @override
  ConsumerState<_KillSwitch> createState() => _KillSwitchState();
}

class _KillSwitchState extends ConsumerState<_KillSwitch> {
  bool _busy = false;

  @override
  Widget build(BuildContext context) {
    final ap = (widget.s['autopilot'] as Map?) ?? const {};
    final stopped = ap['stopped'] == true;
    final cooldowns = ((ap['cooldowns'] as Map?) ?? const {}).cast<String, dynamic>();
    return Card(
      child: Column(children: [
        SwitchListTile(
          secondary: Icon(stopped ? Icons.stop_circle : Icons.smart_toy_outlined),
          title: const Text('Autopilot allowed'),
          subtitle: Text(stopped ? 'Kill switch ON - every stage stops at its next check' : 'Running stages may continue'),
          value: !stopped,
          onChanged: _busy
              ? null
              : (allow) async {
                  setState(() => _busy = true);
                  try {
                    await requireClientFrom(ref).call('autopilot_stop', {'on': !allow});
                    ref.invalidate(statusProvider);
                  } catch (e) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$e')));
                    }
                  } finally {
                    if (mounted) setState(() => _busy = false);
                  }
                },
        ),
        for (final c in cooldowns.entries)
          ListTile(dense: true, leading: const Icon(Icons.timer_outlined), title: Text('${c.key} on cooldown'),
              subtitle: Text('${c.value}')),
      ]),
    );
  }
}

class _QuickActions extends StatelessWidget {
  const _QuickActions();

  @override
  Widget build(BuildContext context) {
    final actions = <(IconData, String, String, Map<String, dynamic>)>[
      (Icons.smart_toy, 'Autopilot (dry run)', 'autopilot_run', {'dry_run': true}),
      (Icons.travel_explore, 'Search all boards', 'multi_board_search', {'save': true}),
      (Icons.search, 'LinkedIn search', 'search_jobs', {}),
      (Icons.assignment_turned_in, 'Easy apply', 'apply_jobs', {'dry_run': true}),
      (Icons.contact_mail, 'Find HR emails', 'find_hr_emails', {}),
      (Icons.forward_to_inbox, 'Email HR (preview)', 'send_hr_emails', {'dry_run': true}),
      (Icons.dynamic_feed, 'Scan hiring posts', 'scan_hiring_posts', {}),
      (Icons.insights, 'Tracker report', 'tracker_report', {}),
    ];
    return GridView.count(
      crossAxisCount: 2,
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      childAspectRatio: 2.6,
      mainAxisSpacing: 8,
      crossAxisSpacing: 8,
      children: [
        for (final (icon, label, tool, args) in actions)
          FilledButton.tonalIcon(
            style: FilledButton.styleFrom(alignment: Alignment.centerLeft, padding: const EdgeInsets.symmetric(horizontal: 12)),
            onPressed: () => context.push('/tools/$tool', extra: args),
            icon: Icon(icon),
            label: Text(label, maxLines: 2),
          ),
      ],
    );
  }
}
