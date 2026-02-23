"""Hypothesis strategies for property-based testing."""

from hypothesis import strategies as st

# MBAP header: 7 bytes
# Transaction ID (2 bytes), Protocol ID (2 bytes), Length (2 bytes), Unit ID (1 byte)
mbap_headers = st.builds(
    lambda tid, proto, length, unit_id: bytes([
        (tid >> 8) & 0xFF, tid & 0xFF,  # Transaction ID (big endian)
        (proto >> 8) & 0xFF, proto & 0xFF,  # Protocol ID (big endian)
        (length >> 8) & 0xFF, length & 0xFF,  # Length (big endian)
        unit_id & 0xFF,  # Unit ID
    ]),
    tid=st.integers(min_value=0, max_value=0xFFFF),
    proto=st.integers(min_value=0, max_value=0xFFFF),
    length=st.integers(min_value=0, max_value=0xFFFF),
    unit_id=st.integers(min_value=0, max_value=0xFF),
)

# Gateway PDU: variable length
# Function code (1 byte), MEI type (1 byte), Protocol control (1 byte), etc.
gateway_pdu = st.builds(
    lambda func, mei, proto_ctrl, byte_count, data: bytes([
        func & 0xFF,
        mei & 0xFF,
        proto_ctrl & 0xFF,
        0x00,  # Reserved
        0x00,  # Node ID
        # Index (2 bytes)
        # Subindex (1 byte)
        # Starting address (3 bytes, all 0)
        # Byte count (1 byte)
        # Data (variable)
    ]) + data,
    func=st.integers(min_value=0, max_value=0xFF),
    mei=st.integers(min_value=0, max_value=0xFF),
    proto_ctrl=st.integers(min_value=0, max_value=1),
    byte_count=st.integers(min_value=0, max_value=4),
    data=st.binary(min_size=0, max_size=4),
)

# Complete ADU (MBAP + PDU)
# Generate ADUs that are more likely to be valid gateway telegrams
adus = st.one_of(
    # Valid-sized ADUs (more likely to pass validation)
    st.builds(
        lambda mbap, pdu: mbap + pdu,
        mbap=mbap_headers,
        pdu=st.binary(min_size=12, max_size=25),  # Typical gateway PDU size
    ),
    # Random ADUs (for fuzzing)
    st.binary(min_size=0, max_size=200),
)

# Statusword: 16-bit value
statuswords = st.integers(min_value=0, max_value=0xFFFF)

# Controlword: 16-bit value
controlwords = st.integers(min_value=0, max_value=0xFFFF)

# OD Indices: 16-bit values (common ranges)
indices = st.one_of(
    st.integers(min_value=0x6000, max_value=0x60FF),  # Standard CiA402
    st.integers(min_value=0x2000, max_value=0x2FFF),  # Vendor-specific
    st.integers(min_value=0x0000, max_value=0xFFFF),  # Any index
)

# Transaction IDs
transaction_ids = st.integers(min_value=0, max_value=0xFFFF)

# Signed/unsigned integers for codec
signed_i32 = st.integers(min_value=-(2**31), max_value=2**31 - 1)
unsigned_u16 = st.integers(min_value=0, max_value=0xFFFF)
unsigned_u32 = st.integers(min_value=0, max_value=0xFFFFFFFF)

# Byte sizes for codec
byte_sizes = st.integers(min_value=1, max_value=4)

