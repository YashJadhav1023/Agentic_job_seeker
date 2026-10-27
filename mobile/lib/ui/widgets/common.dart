import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../api/client.dart';
import '../theme.dart';

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
            padding: const EdgeInsets.fromLTRB(16, 16, 44, 16),
            child: SelectableText(
              text,
              style: const TextStyle(
                fontFamily: 'monospace',
                fontSize: 12.5,
                color: AppTheme.textPrimary,
                height: 1.4,
              ),
            ),
          ),
          Positioned(
            right: 4,
            top: 4,
            child: IconButton(
              tooltip: 'Copy',
              icon: const Icon(Icons.copy, size: 18, color: AppTheme.textSecondary),
              onPressed: () {
                Clipboard.setData(ClipboardData(text: text));
                ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied to clipboard')));
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
      loading: () => const Center(
        child: SizedBox(
          width: 32,
          height: 32,
          child: CircularProgressIndicator(strokeWidth: 2.5, color: AppTheme.primaryLight),
        ),
      ),
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
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: AppTheme.error.withValues(alpha: 0.12),
                shape: BoxShape.circle,
              ),
              child: Icon(
                notConfigured ? Icons.settings_ethernet : Icons.cloud_off,
                size: 40,
                color: AppTheme.error,
              ),
            ),
            const SizedBox(height: 16),
            Text(
              notConfigured
                  ? 'Connect to your server first'
                  : unauth
                      ? 'The API token was rejected'
                      : 'Connection error',
              style: Theme.of(context).textTheme.titleMedium?.copyWith(
                    fontWeight: FontWeight.w700,
                    color: AppTheme.textPrimary,
                  ),
            ),
            const SizedBox(height: 8),
            Text(
              '$error',
              textAlign: TextAlign.center,
              style: const TextStyle(color: AppTheme.textSecondary, fontSize: 13),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh, size: 18),
              label: const Text('Try again'),
            ),
          ],
        ),
      ),
    );
  }
}

/// Modern coloured pill label with indicator dot.
class StatusChip extends StatelessWidget {
  const StatusChip(this.label, {super.key, this.isMini = false});
  final String label;
  final bool isMini;

  @override
  Widget build(BuildContext context) {
    final l = label.toLowerCase();
    final (Color bg, Color fg, Color dot) = switch (l) {
      'ok' || 'applied' || 'emailed' || 'sent' || 'valid' || 'high' || 'active' || 'logged in' => (
        AppTheme.success.withValues(alpha: 0.14),
        AppTheme.successLight,
        AppTheme.success
      ),
      'running' || 'in_progress' => (
        AppTheme.primary.withValues(alpha: 0.18),
        AppTheme.primaryLight,
        AppTheme.primaryLight
      ),
      'new' || 'queued' => (
        AppTheme.secondary.withValues(alpha: 0.15),
        AppTheme.secondaryLight,
        AppTheme.secondary
      ),
      'failed' || 'error' || 'invalid' || 'blocked' => (
        AppTheme.error.withValues(alpha: 0.15),
        const Color(0xFFFCA5A5),
        AppTheme.error
      ),
      'dry_run' || 'medium' || 'warning' => (
        AppTheme.warning.withValues(alpha: 0.14),
        AppTheme.warningLight,
        AppTheme.warning
      ),
      _ => (
        AppTheme.cardElevated,
        AppTheme.textSecondary,
        AppTheme.textMuted
      ),
    };

    return Container(
      padding: EdgeInsets.symmetric(
        horizontal: isMini ? 6 : 8,
        vertical: isMini ? 2 : 3,
      ),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: fg.withValues(alpha: 0.2), width: 0.8),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: isMini ? 4 : 5,
            height: isMini ? 4 : 5,
            decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
          ),
          SizedBox(width: isMini ? 4 : 5),
          Text(
            label,
            style: TextStyle(
              color: fg,
              fontSize: isMini ? 11 : 12,
              fontWeight: FontWeight.w600,
              letterSpacing: -0.1,
            ),
          ),
        ],
      ),
    );
  }
}

/// Clean Section Header with an icon and optional trailing action.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.title,
    this.icon,
    this.trailing,
    this.padding = const EdgeInsets.symmetric(horizontal: 4, vertical: 8),
  });

  final String title;
  final IconData? icon;
  final Widget? trailing;
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: padding,
      child: Row(
        children: [
          if (icon != null) ...[
            Icon(icon, size: 18, color: AppTheme.primaryLight),
            const SizedBox(width: 8),
          ],
          Text(
            title,
            style: const TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w700,
              color: AppTheme.textPrimary,
              letterSpacing: -0.3,
            ),
          ),
          const Spacer(),
          ?trailing,
        ],
      ),
    );
  }
}

/// Modern KPI / Metric card for stats with progress indicators.
class MetricCard extends StatelessWidget {
  const MetricCard({
    super.key,
    required this.title,
    required this.value,
    required this.cap,
    required this.icon,
    required this.accentColor,
  });

  final String title;
  final num value;
  final num cap;
  final IconData icon;
  final Color accentColor;

  @override
  Widget build(BuildContext context) {
    final progress = cap == 0 ? 0.0 : (value / cap).clamp(0.0, 1.0).toDouble();

    return Expanded(
      child: Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: AppTheme.card,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(color: AppTheme.borderSubtle),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: accentColor.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(icon, size: 16, color: accentColor),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    title,
                    style: const TextStyle(
                      color: AppTheme.textSecondary,
                      fontSize: 12.5,
                      fontWeight: FontWeight.w500,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Text(
                  '$value',
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w800,
                    color: AppTheme.textPrimary,
                    letterSpacing: -0.5,
                  ),
                ),
                Text(
                  ' / $cap',
                  style: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                    color: AppTheme.textMuted,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                value: progress,
                minHeight: 5,
                backgroundColor: AppTheme.surface,
                valueColor: AlwaysStoppedAnimation<Color>(accentColor),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
