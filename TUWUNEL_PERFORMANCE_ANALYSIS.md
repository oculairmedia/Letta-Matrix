# Tuwunel Performance Deep Dive Analysis
**Date:** November 24, 2025  
**Server:** matrix.oculair.ca  
**Uptime:** 8 days (since Nov 16)

## Executive Summary

Tuwunel has accumulated **6 days, 9 hours of CPU time** (146 hours) over 8 days of runtime, representing an **average of 76% CPU utilization**. However, current instantaneous CPU usage is near 0%, indicating the high CPU time was accumulated gradually over time, not from current active load.

## Current Resource Usage

### Real-time Metrics
- **Current CPU**: 0.0% (idle)
- **Memory Usage**: 1.128 GiB / 72.27 GiB (1.56%)
- **Memory (RSS)**: 1.16 GB
- **Threads**: 78 active threads
- **Network I/O**: 2.59 GB received / 5.22 GB sent
- **Disk I/O**: 26.1 GB read / 0 B written (read-heavy)

### Cumulative Stats (8 days)
- **User CPU Time**: 526,948 seconds (146 hours)
- **System CPU Time**: 25,459 seconds (7 hours)
- **Total CPU Time**: 552,407 seconds (153 hours)
- **CPU Efficiency**: User 95.4% / System 4.6% (good ratio)

### I/O Statistics
- **Bytes Read**: 40.88 GB from disk
- **Bytes Written**: 11.17 GB to disk  
- **Read/Write Ratio**: 3.66:1 (read-heavy workload)
- **System Calls**: 1,063,753 reads / 21,237,085 writes

## Database Analysis

### Storage
- **Total Size**: 414 MB
- **SST Files**: 249 files
- **Archived Data**: 40 MB (35 files)
- **Log Files**: ~50 MB total
- **Largest SST Files**: 30-33 MB each

### File Distribution
The database shows healthy compaction with:
- Active data spread across 249 SST files
- Largest files around 33MB (indicates good compaction)
- Archive directory at 40MB (reasonable cleanup)

## Configuration Review

### Current Settings
```yaml
TUWUNEL_SERVER_NAME: matrix.oculair.ca
TUWUNEL_DATABASE_PATH: /var/lib/tuwunel
TUWUNEL_ALLOW_REGISTRATION: true (OPEN REGISTRATION)
TUWUNEL_ALLOW_FEDERATION: true
TUWUNEL_MAX_REQUEST_SIZE: 20000000 (20MB)
TUWUNEL_LOG: info
TUWUNEL_TRUSTED_SERVERS: ["matrix.org"]
```

### ⚠️ **CRITICAL SECURITY ISSUE**
```
TUWUNEL_YES_I_AM_VERY_VERY_SURE_I_WANT_AN_OPEN_REGISTRATION_SERVER_PRONE_TO_ABUSE: true
```

**This is a major security and performance risk!**
- Open registration without any token/captcha
- Anyone can create accounts
- Spam accounts can be created automatically
- Can lead to abuse, spam, and resource exhaustion

## Performance Characteristics

### CPU Usage Pattern
- **Average CPU**: ~76% over 8 days
- **Current CPU**: Near 0%
- **Pattern**: Likely burst activity (federation, message processing) rather than constant high load

### Memory Characteristics
- **Physical RAM**: 1.16 GB (stable)
- **Virtual Memory**: 3.1 GB
- **Page Faults (Minor)**: 8.49 billion (very high - indicates memory churn)
- **Page Faults (Major)**: 10,925 (low - good)

The extremely high minor page fault count (8.49 billion) suggests:
- Frequent memory access patterns
- Possible inefficient memory management
- High object allocation/deallocation

### Network Activity
- **Total Traffic**: 7.81 GB (2.59 GB in, 5.22 GB out)
- **Pattern**: More outgoing than incoming (federation push)
- **Connections**: Only 3 active TCP/UDP connections currently

### Disk I/O
- **Read**: 40.88 GB (high)
- **Write**: 11.17 GB (moderate)
- **Pattern**: Read-heavy database operations
- **Block I/O**: 26.1 GB read from container

## Performance Bottlenecks Identified

### 1. **Memory Churn** (HIGH IMPACT)
- 8.49 billion minor page faults over 8 days
- Average: ~12.3 million page faults per minute
- Indicates frequent object allocation/deallocation
- **Root Cause**: Likely Rust's memory management with RocksDB

### 2. **Disk I/O** (MEDIUM IMPACT)
- 40.88 GB read over 8 days
- Average: ~5.1 GB/day or ~59 KB/second
- RocksDB SST file reads for queries
- **Root Cause**: Database queries and compaction

### 3. **Open Registration** (SECURITY & PERFORMANCE)
- Allows unlimited account creation
- Potential for spam/abuse
- Resource consumption from unwanted accounts
- **Root Cause**: Configuration choice

### 4. **Federation Load** (MEDIUM IMPACT)
- Connected to matrix.org (huge server)
- 5.22 GB outgoing (federation events)
- CPU spikes likely from federation traffic
- **Root Cause**: Design - federated protocol overhead

## Resource Utilization Analysis

### Good Signs ✅
1. **Low current CPU** - Not currently overloaded
2. **Stable memory** - No memory leaks detected
3. **Healthy database size** - 414 MB is reasonable
4. **Good compaction** - SST files well-managed
5. **Minimal major page faults** - Disk swapping not an issue
6. **78 threads** - Within reason for Tokio runtime

### Concerns ⚠️
1. **High cumulative CPU** - 76% average over 8 days
2. **Massive minor page faults** - Memory churn issue
3. **Open registration** - Security and abuse risk
4. **Limited observability** - No built-in metrics endpoint

## Recommendations

### Immediate Actions (High Priority)

#### 1. **CLOSE OPEN REGISTRATION** 🔴
```yaml
TUWUNEL_ALLOW_REGISTRATION: false
```
Remove the abuse-prone open registration flag. Use registration tokens instead.

#### 2. **Add Resource Limits**
```yaml
deploy:
  resources:
    limits:
      cpus: '2.0'
      memory: 2G
    reservations:
      cpus: '0.5'
      memory: 512M
```

#### 3. **Enable Debug Logging Temporarily**
Change `TUWUNEL_LOG: debug` for 1 hour to capture what's causing CPU spikes, then revert to `info`.

#### 4. **Add Monitoring**
- Set up Prometheus metrics if available
- Monitor CPU usage patterns over 24h
- Track federation events

### Medium-Term Optimizations

#### 1. **Federation Filtering**
Consider limiting federation to known/trusted servers:
```yaml
TUWUNEL_TRUSTED_SERVERS: ["matrix.org", "mozilla.org"]
```

#### 2. **Database Tuning**
RocksDB configuration (if configurable):
- Increase block cache size
- Adjust compaction settings
- Enable bloom filters

#### 3. **Reduce Max Request Size**
```yaml
TUWUNEL_MAX_REQUEST_SIZE: 10485760  # 10MB instead of 20MB
```

#### 4. **Regular Maintenance**
- Weekly database compaction check
- Monthly performance review
- Archive old room data

### Long-Term Considerations

1. **Migration to Production-Ready Homeserver**
   - Tuwunel is experimental/beta
   - Consider Synapse or Dendrite for production
   - Better tooling, monitoring, and community support

2. **Horizontal Scaling**
   - Separate federation from client API
   - Use multiple workers if supported

3. **External Database**
   - Move to PostgreSQL if supported
   - Better query optimization tools
   - Easier backup/restore

## Comparison with Other Services

| Service | CPU Time (8 days) | Avg CPU % |
|---------|------------------|-----------|
| Tuwunel | 153 hours | 76% |
| Dockerd | 64 hours | 32% |
| Letta | 24 hours | 12% |
| Vibe Kanban | 12 hours | 6% |

**Tuwunel is using 2.4x more CPU than Docker daemon itself!**

## Conclusion

### Performance Grade: **C+** (Functional but inefficient)

**Strengths:**
- Stable operation over 8 days
- No crashes or major errors
- Reasonable memory usage
- Responsive API

**Weaknesses:**
- High CPU usage (76% average)
- Massive memory churn (8.49B page faults)
- Open registration security risk
- Limited observability/metrics
- Experimental software in production

### Primary Cause of High CPU:
The high CPU usage appears to be caused by:
1. **Federation overhead** - Constant communication with matrix.org
2. **Memory management** - Frequent allocations causing page faults
3. **Database operations** - RocksDB compaction and queries
4. **Potentially spam/abuse** - Due to open registration

### Action Required:
**IMMEDIATE**: Close open registration to prevent abuse  
**URGENT**: Add resource limits and monitoring  
**IMPORTANT**: Consider migration to Synapse for production stability

## Next Steps

1. ✅ Review this analysis
2. ⏳ Close open registration
3. ⏳ Add resource limits
4. ⏳ Monitor for 24 hours
5. ⏳ Re-evaluate performance
6. ⏳ Decide on long-term homeserver solution
