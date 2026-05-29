import { api } from '@/lib/api'

export const speedafApi = {
  createWorkOrder: api.speedafCreateWorkOrder,
  addressUpdate: api.speedafAddressUpdate,
  cancelPreview: api.speedafCancelPreview,
  cancel: api.speedafCancel,
}
