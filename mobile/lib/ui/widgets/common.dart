import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/client.dart';

/// Pretty, copyable JSON viewer for raw tool output.
class JsonView extends StatelessWidget {
  const JsonView(this.data, {super.key});
  final dynamic data;

  @override
  Widget build(BuildContext context) {
    final text = const JsonEncoder.withIndent('  ').convert(data);
    return Card(
      child: Stack(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 40, 12),
            child: SelectableText(text, style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5)),
          ),
          Positioned(
            right: 0,
            top: 0,
            child: IconButton(
              tooltip: 'Copy',
              icon: const Icon(Icons.copy, size: 18),
              onPressed: () {
                Clipboard.setData(ClipboardData(text: text));
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
              },
            ),
          ),
        ],
      ),
    );
  }
}

/// Loading / error / data wrapper with pull-to-retry for provider-backed screens.
class AsyncBody<T> extends StatelessWidget {
  const AsyncBody({super.key, required this.value, required this.data, required this.onRetry});
  final AsyncValue<T> value;
  final Widget Function(T) data;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return value.when(
      skipLoadingOnRefresh: true,
      data: data,
      loading: () => const Center(child: CircularProgressIndicator()),
      error: (e, _) => ErrorPanel(error: e, onRetry: onRetry),
    );
  }
}

class ErrorPanel extends StatelessWidget {
  const ErrorPanel({super.key, required this.error, required this.onRetry});
  final Object error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final unauth = error is ApiException && (error as ApiException).unauthorized;
    final notConfigured = error.toString().contains('not configured');
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          Icon(notConfigured ? Icons.settings_ethernet : Icons.cloud_off, size: 48, color: Theme.of(context).colorScheme.error),
          const SizedBox(height: 12),
          Text(
            notConfigured
                ? 'Connect to your server first'
                : unauth
                    ? 'The API token was rejected'
                    : 'Something went wrong',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 8),
          Text('$error', textAlign: TextAlign.center),
          const SizedBox(height: 16),
          FilledButton.tonalIcon(onPressed: onRetry, icon: const Icon(Icons.refresh), label: const Text('Retry')),
        ]),
      ),
    );
  }
}

/// Small coloured label, e.g. job status or run state.
class StatusChip extends StatelessWidget {
  const StatusChip(this.label, {super.key});
  final String label;

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final (bg, fg) = switch (label) {
      'ok' || 'applied' || 'emailed' || 'sent' || 'valid' || 'high' => (Colors.green.shade100, Colors.green.shade900),
      'running' || 'new' || 'queued' || 'medium' => (cs.primaryContainer, cs.onPrimaryContainer),
      'failed' || 'error' || 'invalid' || 'blocked' => (cs.errorContainer, cs.onErrorContainer),
      _ => (cs.surfaceContainerHighest, cs.onSurfaceVariant),
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(color: bg, borderRadius: BorderRadius.circular(12)),
      child: Text(label, style: TextStyle(color: fg, fontSize: 12, fontWeight: FontWeight.w600)),
    );
  }
}
