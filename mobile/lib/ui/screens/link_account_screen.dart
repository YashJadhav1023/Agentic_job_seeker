import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../state/providers.dart';
import '../theme.dart';
import '../widgets/common.dart';
import 'tool_run_screen.dart' show requireClientFrom;

const _platforms = <(String, String, IconData)>[
  ('linkedin', 'LinkedIn', Icons.business_center_rounded),
  ('naukri', 'Naukri', Icons.work_rounded),
  ('indeed', 'Indeed', Icons.search_rounded),
];

/// One-tap account linking. "Link" asks the server to open its sign-in browser and opens a single-use link to it, so
/// there is no extra login page. The screen then waits until the account shows as linked.
class LinkAccountScreen extends ConsumerStatefulWidget {
  const LinkAccountScreen({super.key, this.platform});

  /// Start linking this platform as soon as the screen opens (used by the "Set up" shortcuts).
  final String? platform;

  @override
  ConsumerState<LinkAccountScreen> createState() => _LinkAccountScreenState();
}

class _LinkAccountScreenState extends ConsumerState<LinkAccountScreen> {
  String? _busy; // platform being linked
  String? _message;
  Timer? _poll;
  int _polls = 0;

  @override
  void initState() {
    super.initState();
    final p = widget.platform;
    if (p != null && _platforms.any((e) => e.$1 == p)) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _link(p));
    }
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _link(String platform) async {
    setState(() {
      _busy = platform;
      _message = 'Opening the sign-in browser…';
    });
    try {
      final r = await requireClientFrom(ref).browserSession(platform);
      final url = Uri.parse('${r['url']}');
      final ok = await launchUrl(url, mode: LaunchMode.externalApplication);
      if (!mounted) return;
      setState(() => _message = ok
          ? 'Sign in to ${_label(platform)} in the browser that opened. It closes by itself when you are signed in; this page updates then.'
          : 'Could not open the browser. Open this link manually: $url');
      _waitForLink(platform);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _busy = null;
        _message = 'Could not start: $e';
      });
    }
  }

  void _waitForLink(String platform) {
    _poll?.cancel();
    _polls = 0;
    _poll = Timer.periodic(const Duration(seconds: 3), (t) async {
      _polls++;
      ref.invalidate(accountsProvider);
      final a = await ref.read(accountsProvider.future).catchError((_) => <String, dynamic>{});
      final linked = ((a['platforms'] as Map?) ?? const {})[platform] == true;
      if (!mounted) return;
      if (linked || _polls > 200) {
        t.cancel();
        ref.invalidate(statusProvider);
        setState(() {
          _busy = null;
          _message = linked ? '${_label(platform)} is linked.' : 'Timed out waiting for sign-in. Tap Link to try again.';
        });
      }
    });
  }

  String _label(String id) => _platforms.firstWhere((e) => e.$1 == id).$2;

  @override
  Widget build(BuildContext context) {
    final accounts = ref.watch(accountsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Link accounts')),
      body: AsyncBody<Map<String, dynamic>>(
        value: accounts,
        onRetry: () => ref.invalidate(accountsProvider),
        data: (a) {
          final linked = ((a['platforms'] as Map?) ?? const {}).cast<String, dynamic>();
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              const Text(
                'Sign in once; the session stays on your server and is used for searching and applying. '
                'Your password is typed into the real site and never passes through this app.',
                style: TextStyle(color: AppTheme.textSecondary, fontSize: 13),
              ),
              const SizedBox(height: 14),
              if (_message != null)
                Container(
                  margin: const EdgeInsets.only(bottom: 12),
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: AppTheme.primary.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Row(children: [
                    if (_busy != null)
                      const Padding(
                        padding: EdgeInsets.only(right: 12),
                        child: SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                      ),
                    Expanded(child: Text(_message!, style: const TextStyle(fontSize: 13))),
                  ]),
                ),
              for (final (id, label, icon) in _platforms)
                Card(
                  child: ListTile(
                    leading: Icon(icon, color: linked[id] == true ? AppTheme.success : AppTheme.textMuted),
                    title: Text(label),
                    subtitle: Text(linked[id] == true ? 'Linked' : 'Not linked'),
                    trailing: FilledButton.tonal(
                      onPressed: _busy != null ? null : () => _link(id),
                      child: Text(linked[id] == true ? 'Re-link' : 'Link'),
                    ),
                  ),
                ),
            ],
          );
        },
      ),
    );
  }
}
