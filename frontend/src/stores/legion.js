import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../utils/api'

/**
 * Legion Store - Manages Legion multi-agent system state
 *
 * Handles:
 * - Comms (communications between minions/user)
 * - Minions (agent sessions within a legion)
 * - Hordes (hierarchical groups of minions)
 * - Channels (cross-horde communication groups)
 */
export const useLegionStore = defineStore('legion', () => {
  // ========== STATE ==========

  // Comms per legion (legionId -> Comm[])
  const commsByLegion = ref(new Map())

  // Minions per legion (legionId -> Minion[])
  const minionsByLegion = ref(new Map())

  // Hordes per legion (legionId -> Horde[])
  const hordesByLegion = ref(new Map())

  // Channels per legion (legionId -> Channel[])
  const channelsByLegion = ref(new Map())

  // Channel details per channel (channelId -> ChannelDetails)
  const channelDetailsByChannel = ref(new Map())

  // Channel comms per channel (channelId -> Comm[])
  const channelCommsByChannel = ref(new Map())

  // Currently selected legion
  const currentLegionId = ref(null)

  // ========== COMPUTED ==========

  // Current legion's comms
  const currentComms = computed(() => {
    return commsByLegion.value.get(currentLegionId.value) || []
  })

  // Current legion's minions
  const currentMinions = computed(() => {
    return minionsByLegion.value.get(currentLegionId.value) || []
  })

  // Current legion's hordes
  const currentHordes = computed(() => {
    return hordesByLegion.value.get(currentLegionId.value) || []
  })

  // Current legion's channels
  const currentChannels = computed(() => {
    return channelsByLegion.value.get(currentLegionId.value) || []
  })

  // ========== ACTIONS ==========

  /**
   * Set the current legion
   */
  function setCurrentLegion(legionId) {
    currentLegionId.value = legionId
  }

  /**
   * Load comms (timeline) for a legion
   */
  async function loadTimeline(legionId, limit = 100, offset = 0) {
    try {
      const data = await api.get(
        `/api/legions/${legionId}/timeline?limit=${limit}&offset=${offset}`
      )

      const comms = data.comms || []
      const totalCount = data.total_count || comms.length
      const hasMore = data.has_more || false

      // Sort comms by timestamp (oldest first, like a chat)
      comms.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))

      console.log(`Loaded ${comms.length} of ${totalCount} comms for legion ${legionId}`)

      // Store comms
      commsByLegion.value.set(legionId, comms)

      // Trigger reactivity
      commsByLegion.value = new Map(commsByLegion.value)

      return { comms, totalCount, hasMore }
    } catch (error) {
      console.error('Failed to load timeline:', error)
      throw error
    }
  }

  /**
   * Add a comm to a legion (from WebSocket or API)
   */
  function addComm(legionId, comm) {
    if (!commsByLegion.value.has(legionId)) {
      commsByLegion.value.set(legionId, [])
    }

    const comms = commsByLegion.value.get(legionId)

    // Check if comm already exists (prevent duplicates from multiple WebSocket connections)
    const exists = comms.some(c => c.comm_id === comm.comm_id)
    if (exists) {
      console.log(`Duplicate comm ${comm.comm_id} prevented in timeline`)
      return
    }

    comms.push(comm)

    // Trigger reactivity
    commsByLegion.value = new Map(commsByLegion.value)

    // Also add to channel-specific list if this comm is for a channel
    if (comm.to_channel_id) {
      addChannelComm(comm.to_channel_id, comm)
    }
  }

  /**
   * Add a comm to a channel's comm list (from WebSocket or API)
   */
  function addChannelComm(channelId, comm) {
    if (!channelCommsByChannel.value.has(channelId)) {
      channelCommsByChannel.value.set(channelId, [])
    }

    const comms = channelCommsByChannel.value.get(channelId)

    // Check if comm already exists (prevent duplicates from multiple WebSocket connections)
    const exists = comms.some(c => c.comm_id === comm.comm_id)
    if (exists) {
      console.log(`Duplicate comm ${comm.comm_id} prevented in channel ${channelId}`)
      return
    }

    comms.push(comm)

    // Trigger reactivity
    channelCommsByChannel.value = new Map(channelCommsByChannel.value)
  }

  /**
   * Send a comm to a legion
   */
  async function sendComm(legionId, commData) {
    try {
      const response = await api.post(`/api/legions/${legionId}/comms`, commData)
      const comm = response.comm

      // Don't add to local state here - let the WebSocket broadcast handle it
      // This prevents duplicate entries when the backend broadcasts the comm back

      console.log(`Sent comm ${comm.comm_id} to legion ${legionId}`)
      return comm
    } catch (error) {
      console.error('Failed to send comm:', error)
      throw error
    }
  }

  /**
   * Load minions for a legion
   *
   * Note: Minions are just sessions that belong to a multi-agent project.
   * We don't need a separate API call - they're already in the session store
   * via the project's session_ids list.
   */
  async function loadMinions(legionId) {
    // Minions are loaded automatically via the session store when projects load
    // This function is kept for API compatibility but doesn't need to do anything
    console.log(`Minions for legion ${legionId} are already available in session store`)
    return []
  }

  /**
   * Create a minion in a legion
   */
  async function createMinion(legionId, minionData) {
    try {
      const response = await api.post(`/api/legions/${legionId}/minions`, minionData)
      const minion = response.minion

      // Refresh minions list
      await loadMinions(legionId)

      console.log(`Created minion ${minion.minion_id} in legion ${legionId}`)
      return minion
    } catch (error) {
      console.error('Failed to create minion:', error)
      throw error
    }
  }

  /**
   * Load hordes for a legion
   */
  async function loadHordes(legionId) {
    try {
      const data = await api.get(`/api/legions/${legionId}/hordes`)
      const hordes = data.hordes || []

      console.log(`Loaded ${hordes.length} hordes for legion ${legionId}`)

      // Store hordes
      hordesByLegion.value.set(legionId, hordes)

      // Trigger reactivity
      hordesByLegion.value = new Map(hordesByLegion.value)

      return hordes
    } catch (error) {
      console.error('Failed to load hordes:', error)
      throw error
    }
  }

  /**
   * Load channels for a legion
   */
  async function loadChannels(legionId) {
    try {
      const data = await api.get(`/api/legions/${legionId}/channels`)
      const channels = data.channels || []

      console.log(`Loaded ${channels.length} channels for legion ${legionId}`)

      // Store channels
      channelsByLegion.value.set(legionId, channels)

      // Trigger reactivity
      channelsByLegion.value = new Map(channelsByLegion.value)

      return channels
    } catch (error) {
      console.error('Failed to load channels:', error)
      throw error
    }
  }

  /**
   * Load channel details
   */
  async function loadChannelDetails(channelId) {
    try {
      const data = await api.get(`/api/channels/${channelId}`)
      const channel = data.channel || data

      console.log(`Loaded details for channel ${channelId}`)

      // Store channel details
      channelDetailsByChannel.value.set(channelId, channel)

      // Trigger reactivity
      channelDetailsByChannel.value = new Map(channelDetailsByChannel.value)

      return channel
    } catch (error) {
      console.error('Failed to load channel details:', error)
      throw error
    }
  }

  /**
   * Load channel comms (first 1000 messages, no pagination for MVP)
   */
  async function loadChannelComms(channelId, limit = 1000) {
    try {
      const data = await api.get(`/api/channels/${channelId}/comms?limit=${limit}`)
      const comms = data.comms || []

      console.log(`Loaded ${comms.length} comms for channel ${channelId}`)

      // Sort comms by timestamp (oldest first, like a chat)
      comms.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))

      // Store channel comms
      channelCommsByChannel.value.set(channelId, comms)

      // Trigger reactivity
      channelCommsByChannel.value = new Map(channelCommsByChannel.value)

      return comms
    } catch (error) {
      console.error('Failed to load channel comms:', error)
      throw error
    }
  }

  /**
   * Add a member to a channel
   * User (self_id: "00000000-0000-0000-0000-000000000000") can add any minion
   */
  async function addMemberToChannel(channelId, minionId) {
    try {
      const USER_MINION_ID = '00000000-0000-0000-0000-000000000000'

      await api.post(`/api/channels/${channelId}/members`, {
        action: 'add',
        self_id: USER_MINION_ID,
        member_id: minionId
      })

      console.log(`Added minion ${minionId} to channel ${channelId}`)

      // Reload channel details to get updated member list
      await loadChannelDetails(channelId)
    } catch (error) {
      console.error('Failed to add member to channel:', error)
      throw error
    }
  }

  /**
   * Remove a member from a channel
   * User (self_id: "00000000-0000-0000-0000-000000000000") can remove any minion
   */
  async function removeMemberFromChannel(channelId, minionId) {
    try {
      const USER_MINION_ID = '00000000-0000-0000-0000-000000000000'

      await api.post(`/api/channels/${channelId}/members`, {
        action: 'remove',
        self_id: USER_MINION_ID,
        member_id: minionId
      })

      console.log(`Removed minion ${minionId} from channel ${channelId}`)

      // Reload channel details to get updated member list
      await loadChannelDetails(channelId)
    } catch (error) {
      console.error('Failed to remove member from channel:', error)
      throw error
    }
  }

  /**
   * Clear all legion data
   */
  function clearLegionData(legionId) {
    commsByLegion.value.delete(legionId)
    minionsByLegion.value.delete(legionId)
    hordesByLegion.value.delete(legionId)
    channelsByLegion.value.delete(legionId)

    // Trigger reactivity
    commsByLegion.value = new Map(commsByLegion.value)
    minionsByLegion.value = new Map(minionsByLegion.value)
    hordesByLegion.value = new Map(hordesByLegion.value)
    channelsByLegion.value = new Map(channelsByLegion.value)
  }

  // ========== RETURN ==========
  return {
    // State
    commsByLegion,
    minionsByLegion,
    hordesByLegion,
    channelsByLegion,
    channelDetailsByChannel,
    channelCommsByChannel,
    currentLegionId,

    // Computed
    currentComms,
    currentMinions,
    currentHordes,
    currentChannels,

    // Actions
    setCurrentLegion,
    loadTimeline,
    addComm,
    addChannelComm,
    sendComm,
    loadMinions,
    createMinion,
    loadHordes,
    loadChannels,
    loadChannelDetails,
    loadChannelComms,
    addMemberToChannel,
    removeMemberFromChannel,
    clearLegionData
  }
})
