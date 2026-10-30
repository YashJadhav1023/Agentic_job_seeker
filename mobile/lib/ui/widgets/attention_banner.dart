import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../state/providers.dart';
import '../screens/tool_run_screen.dart' show requireClientFrom;
import '../theme.dart';

/// "The bot needs you": a site asks for your login or a human check. Tap Open, finish that step in the remote browser,
/// and the bot carries on by itself. Hidden when nothing is waiting. Refreshes every few seconds.
class AttentionBanner extends ConsumerStatefulWidget {
  const AttentionBanner({super.key});

  @override
  ConsumerState<AttentionBanner> createState() => _AttentionBannerState();
}

class _AttentionBannerState extends ConsumerState<AttentionBanner> {
  Timer? _t;

  @override
  void initState() {
    super.initState();
    _t = Timer.periodic(const Duration(seconds: 8), (_) {
      if (mounted) ref.invalidate(attentionProvider);
    });
  }

  @override
  void dispose() {
    _t?.cancel();
    super.dispose();
  }

  Future<void> _skip(int id) async {
    await requireClientFrom(ref).call('attention_resolve', {'id': id, 'status': 'skipped'});
    ref.invalidate(attentionProvider);
  }

  @override
  Widget build(BuildContext context) {
    final items = ref.watch(attentionProvider).value ?? const [];
    if (items.isEmpty) return const SizedBox.shrink();
    return Column(children: [
      for (final r in items)
        Padding(
          padding: const EdgeInsets.only(bottom: 12),
          child: Container(
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: AppTheme.card,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: const Color(0xFFFBBF24).withValues(alpha: 0.6)),
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Row(children: [
                const Icon(Icons.front_hand_rounded, color: Color(0xFFFBBF24)),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    r['kind'] == 'login' ? 'Sign in to ${r['site']}' : '${r['site']} wants to check you are human',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Text('${r['minutes_left'] ?? ''} min', style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12)),
              ]),
              if ('${r['job'] ?? ''}'.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('${r['job']}', style: const TextStyle(color: AppTheme.textSecondary, fontSize: 12.5)),
                ),
              const SizedBox(height: 8),
              Row(mainAxisAlignment: MainAxisAlignment.end, children: [
                TextButton(onPressed: () => _skip(r['id'] as int), child: const Text('Skip this job')),
                const SizedBox(width: 6),
                FilledButton.icon(
                  icon: const Icon(Icons.open_in_browser_rounded, size: 18),
                  label: const Text('Open'),
                  onPressed: '${r['open_url'] ?? ''}'.isEmpty
                      ? null
                      : () => launchUrl(Uri.parse('${r['open_url']}'), mode: LaunchMode.externalApplication),
                ),
              ]),
            ]),
          ),
        ),
    ]);
  }
}
