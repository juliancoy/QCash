#include "mining_compact32.hpp"
#include "mining_compact64.hpp"
#include "mining_pair32.hpp"
#include "mining_prepare.hpp"
#include "mining_scalar64.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <cstring>
#include <limits>
#include <pqcoin/vulkan_miner.hpp>
#include <sstream>
#include <stdexcept>
#include <vulkan/vulkan.h>
namespace pqcoin {
namespace {
void need(bool value, const char *message) {
  if (!value)
    throw std::runtime_error(message);
}
void vkcheck(VkResult result) {
  if (result != VK_SUCCESS)
    throw std::runtime_error("Vulkan operation failed: " +
                             std::to_string(result));
}
constexpr std::uint64_t rc[] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL};
constexpr unsigned rho[] = {0,  1,  62, 28, 27, 36, 44, 6,  55, 20, 3,  10, 43,
                            25, 39, 41, 45, 15, 21, 8,  18, 2,  61, 56, 14};
void keccak(std::array<std::uint64_t, 25> &a) {
  for (auto constant : rc) {
    std::array<std::uint64_t, 5> c{}, d{};
    std::array<std::uint64_t, 25> b{};
    for (unsigned x = 0; x < 5; x++)
      c[x] = a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20];
    for (unsigned x = 0; x < 5; x++)
      d[x] = c[(x + 4) % 5] ^ std::rotl(c[(x + 1) % 5], 1);
    for (unsigned y = 0; y < 5; y++)
      for (unsigned x = 0; x < 5; x++) {
        auto i = x + 5 * y;
        b[y + 5 * ((2 * x + 3 * y) % 5)] =
            std::rotl(a[i] ^ d[x], static_cast<int>(rho[i]));
      }
    for (unsigned y = 0; y < 5; y++)
      for (unsigned x = 0; x < 5; x++)
        a[x + 5 * y] =
            b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]);
    a[0] ^= constant;
  }
}
struct alignas(8) Job {
  std::array<std::uint64_t, 25> midstate{};
  std::array<std::uint64_t, 17> tail{};
  std::array<std::uint32_t, 8> target{};
};
static_assert(sizeof(Job) == 368);
} // namespace
struct VulkanMiner::Impl {
  VkInstance instance{};
  VkPhysicalDevice physical{};
  VkDevice device{};
  VkQueue queue{};
  std::uint32_t family{}, index{}, group{};
  VkPhysicalDeviceProperties properties{};
  VkDescriptorSetLayout descriptor_layout{};
  VkPipelineLayout pipeline_layout{};
  VkShaderModule shader{};
  VkPipeline pipeline{};
  VkShaderModule prepare_shader{};
  VkPipeline prepare_pipeline{};
  VkDescriptorPool descriptor_pool{};
  VkDescriptorSet descriptors{};
  VkCommandPool command_pool{};
  VkCommandBuffer command{};
  VkFence fence{};
  VkQueryPool queries{};
  unsigned timestamp_bits{};
  bool diagnostics = false;
  struct Buffer {
    VkBuffer buffer{};
    VkDeviceMemory memory{};
    void *mapped{};
    bool coherent{};
  } input, output, parameters;
  bool prepared = false, pending_prepare = false;
  std::uint32_t cached_count = 0, cached_dispatch = 0;
  bool cached_prepare = false;
  std::uint64_t recordings = 0;
  ~Impl() {
    if (device) {
      vkDeviceWaitIdle(device);
      if (queries)
        vkDestroyQueryPool(device, queries, nullptr);
      if (fence)
        vkDestroyFence(device, fence, nullptr);
      if (command_pool)
        vkDestroyCommandPool(device, command_pool, nullptr);
      if (pipeline)
        vkDestroyPipeline(device, pipeline, nullptr);
      if (prepare_pipeline)
        vkDestroyPipeline(device, prepare_pipeline, nullptr);
      if (prepare_shader)
        vkDestroyShaderModule(device, prepare_shader, nullptr);
      if (shader)
        vkDestroyShaderModule(device, shader, nullptr);
      if (pipeline_layout)
        vkDestroyPipelineLayout(device, pipeline_layout, nullptr);
      if (descriptor_pool)
        vkDestroyDescriptorPool(device, descriptor_pool, nullptr);
      if (descriptor_layout)
        vkDestroyDescriptorSetLayout(device, descriptor_layout, nullptr);
      for (auto *b : {&parameters, &output, &input}) {
        if (b->mapped)
          vkUnmapMemory(device, b->memory);
        if (b->buffer)
          vkDestroyBuffer(device, b->buffer, nullptr);
        if (b->memory)
          vkFreeMemory(device, b->memory, nullptr);
      }
      vkDestroyDevice(device, nullptr);
    }
    if (instance)
      vkDestroyInstance(instance, nullptr);
  }
  void buffer(Buffer &out, VkDeviceSize size) {
    VkBufferCreateInfo info{VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO};
    info.size = size;
    info.usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    vkcheck(vkCreateBuffer(device, &info, nullptr, &out.buffer));
    VkMemoryRequirements required{};
    vkGetBufferMemoryRequirements(device, out.buffer, &required);
    VkPhysicalDeviceMemoryProperties memory{};
    vkGetPhysicalDeviceMemoryProperties(physical, &memory);
    int best = -1, score = -1;
    for (std::uint32_t i = 0; i < memory.memoryTypeCount; i++) {
      auto flags = memory.memoryTypes[i].propertyFlags;
      if (!(required.memoryTypeBits & (1U << i)) ||
          !(flags & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT))
        continue;
      int candidate = ((flags & VK_MEMORY_PROPERTY_HOST_COHERENT_BIT) ? 2 : 0) +
                      ((flags & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) ? 1 : 0);
      if (candidate > score) {
        best = static_cast<int>(i);
        score = candidate;
      }
    }
    need(best >= 0, "No host-visible Vulkan memory");
    out.coherent = memory.memoryTypes[best].propertyFlags &
                   VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    VkMemoryAllocateInfo allocation{VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    allocation.allocationSize = required.size;
    allocation.memoryTypeIndex = static_cast<std::uint32_t>(best);
    vkcheck(vkAllocateMemory(device, &allocation, nullptr, &out.memory));
    vkcheck(vkBindBufferMemory(device, out.buffer, out.memory, 0));
    vkcheck(vkMapMemory(device, out.memory, 0, VK_WHOLE_SIZE, 0, &out.mapped));
  }
  void cache(Buffer &b, bool flush) {
    if (b.coherent)
      return;
    VkMappedMemoryRange range{VK_STRUCTURE_TYPE_MAPPED_MEMORY_RANGE};
    range.memory = b.memory;
    range.size = VK_WHOLE_SIZE;
    vkcheck(flush ? vkFlushMappedMemoryRanges(device, 1, &range)
                  : vkInvalidateMappedMemoryRanges(device, 1, &range));
  }
  void init(std::optional<std::uint32_t> requested, std::uint32_t workgroup,
            const std::string &variant, bool capture_statistics) {
    need(workgroup == 32 || workgroup == 64 || workgroup == 128 ||
             workgroup == 256,
         "Workgroup must be 32, 64, 128 or 256");
    group = workgroup;
    VkApplicationInfo app{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    app.pApplicationName = "QCash Vulkan miner";
    app.apiVersion = VK_API_VERSION_1_1;
    VkInstanceCreateInfo info{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    info.pApplicationInfo = &app;
    vkcheck(vkCreateInstance(&info, nullptr, &instance));
    std::uint32_t count = 0;
    vkcheck(vkEnumeratePhysicalDevices(instance, &count, nullptr));
    std::vector<VkPhysicalDevice> devices(count);
    vkcheck(vkEnumeratePhysicalDevices(instance, &count, devices.data()));
    bool selected = false;
    for (std::uint32_t i = 0; i < count; i++) {
      VkPhysicalDeviceProperties props{};
      vkGetPhysicalDeviceProperties(devices[i], &props);
      if (requested
              ? i != *requested
              : (props.deviceType != VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU &&
                 props.deviceType != VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU))
        continue;
      VkPhysicalDeviceFeatures features{};
      vkGetPhysicalDeviceFeatures(devices[i], &features);
      if (!features.shaderInt64)
        continue;
      std::uint32_t n = 0;
      vkGetPhysicalDeviceQueueFamilyProperties(devices[i], &n, nullptr);
      std::vector<VkQueueFamilyProperties> families(n);
      vkGetPhysicalDeviceQueueFamilyProperties(devices[i], &n, families.data());
      for (std::uint32_t j = 0; j < n; j++)
        if (families[j].queueFlags & VK_QUEUE_COMPUTE_BIT) {
          physical = devices[i];
          properties = props;
          index = i;
          family = j;
          timestamp_bits = families[j].timestampValidBits;
          selected = true;
          break;
        }
      if (selected)
        break;
    }
    need(selected, "No eligible Vulkan GPU with shaderInt64 (no automatic "
                   "software fallback)");
    need(group <= properties.limits.maxComputeWorkGroupInvocations &&
             group <= properties.limits.maxComputeWorkGroupSize[0],
         "Unsupported workgroup size");
    need((max_dispatch + group - 1) / group <=
             properties.limits.maxComputeWorkGroupCount[0],
         "GPU dispatch limit too small");
    float priority = 1;
    VkDeviceQueueCreateInfo qinfo{VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO};
    qinfo.queueFamilyIndex = family;
    qinfo.queueCount = 1;
    qinfo.pQueuePriorities = &priority;
    VkPhysicalDeviceFeatures enabled{};
    enabled.shaderInt64 = VK_TRUE;
    VkDeviceCreateInfo dinfo{VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO};
    dinfo.queueCreateInfoCount = 1;
    dinfo.pQueueCreateInfos = &qinfo;
    dinfo.pEnabledFeatures = &enabled;
    VkPhysicalDevicePipelineExecutablePropertiesFeaturesKHR executable{
        VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PIPELINE_EXECUTABLE_PROPERTIES_FEATURES_KHR};
    const char *extension =
        VK_KHR_PIPELINE_EXECUTABLE_PROPERTIES_EXTENSION_NAME;
    if (capture_statistics) {
      std::uint32_t extension_count = 0;
      vkcheck(vkEnumerateDeviceExtensionProperties(physical, nullptr,
                                                   &extension_count, nullptr));
      std::vector<VkExtensionProperties> available(extension_count);
      vkcheck(vkEnumerateDeviceExtensionProperties(
          physical, nullptr, &extension_count, available.data()));
      bool supported =
          std::any_of(available.begin(), available.end(), [&](const auto &e) {
            return std::strcmp(e.extensionName, extension) == 0;
          });
      if (supported) {
        VkPhysicalDeviceFeatures2 f{
            VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
        f.pNext = &executable;
        vkGetPhysicalDeviceFeatures2(physical, &f);
        if (executable.pipelineExecutableInfo) {
          diagnostics = true;
          dinfo.enabledExtensionCount = 1;
          dinfo.ppEnabledExtensionNames = &extension;
          dinfo.pNext = &executable;
        }
      }
    }
    vkcheck(vkCreateDevice(physical, &dinfo, nullptr, &device));
    vkGetDeviceQueue(device, family, 0, &queue);
    buffer(input, sizeof(Job));
    buffer(parameters, 16);
    buffer(output, 16 + 4096 * 32);
    VkDescriptorSetLayoutBinding bindings[3]{};
    for (unsigned i = 0; i < 3; i++) {
      bindings[i].binding = i;
      bindings[i].descriptorCount = 1;
      bindings[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
      bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo linfo{
        VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO};
    linfo.bindingCount = 3;
    linfo.pBindings = bindings;
    vkcheck(vkCreateDescriptorSetLayout(device, &linfo, nullptr,
                                        &descriptor_layout));
    VkPushConstantRange push{VK_SHADER_STAGE_COMPUTE_BIT, 0, 8};
    VkPipelineLayoutCreateInfo plinfo{
        VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO};
    plinfo.setLayoutCount = 1;
    plinfo.pSetLayouts = &descriptor_layout;
    plinfo.pushConstantRangeCount = 1;
    plinfo.pPushConstantRanges = &push;
    vkcheck(vkCreatePipelineLayout(device, &plinfo, nullptr, &pipeline_layout));
    VkShaderModuleCreateInfo sinfo{VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO};
    auto select = [&](auto &code) {
      sinfo.codeSize = sizeof(code);
      sinfo.pCode = code;
    };
    if (variant == "scalar64")
      select(mining_scalar64);
    else if (variant == "pair32")
      select(mining_pair32);
    else if (variant == "compact64")
      select(mining_compact64);
    else if (variant == "compact32")
      select(mining_compact32);
    else
      throw std::runtime_error("Unknown shader variant");
    vkcheck(vkCreateShaderModule(device, &sinfo, nullptr, &shader));
    VkSpecializationMapEntry entry{0, 0, sizeof(group)};
    VkSpecializationInfo specialization{1, &entry, sizeof(group), &group};
    VkComputePipelineCreateInfo pinfo{
        VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO};
    pinfo.layout = pipeline_layout;
    if (diagnostics)
      pinfo.flags = VK_PIPELINE_CREATE_CAPTURE_STATISTICS_BIT_KHR;
    pinfo.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    pinfo.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    pinfo.stage.module = shader;
    pinfo.stage.pName = "main";
    pinfo.stage.pSpecializationInfo = &specialization;
    vkcheck(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pinfo, nullptr,
                                     &pipeline));
    sinfo.codeSize = sizeof(mining_prepare);
    sinfo.pCode = mining_prepare;
    vkcheck(vkCreateShaderModule(device, &sinfo, nullptr, &prepare_shader));
    pinfo.stage.module = prepare_shader;
    pinfo.stage.pSpecializationInfo = nullptr;
    vkcheck(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pinfo, nullptr,
                                     &prepare_pipeline));
    VkDescriptorPoolSize poolsize{VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 3};
    VkDescriptorPoolCreateInfo dpinfo{
        VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO};
    dpinfo.maxSets = 1;
    dpinfo.poolSizeCount = 1;
    dpinfo.pPoolSizes = &poolsize;
    vkcheck(vkCreateDescriptorPool(device, &dpinfo, nullptr, &descriptor_pool));
    VkDescriptorSetAllocateInfo dsinfo{
        VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO};
    dsinfo.descriptorPool = descriptor_pool;
    dsinfo.descriptorSetCount = 1;
    dsinfo.pSetLayouts = &descriptor_layout;
    vkcheck(vkAllocateDescriptorSets(device, &dsinfo, &descriptors));
    VkDescriptorBufferInfo buffers[] = {{input.buffer, 0, sizeof(Job)},
                                        {output.buffer, 0, 16 + 4096 * 32},
                                        {parameters.buffer, 0, 16}};
    VkWriteDescriptorSet writes[3]{};
    for (unsigned i = 0; i < 3; i++) {
      writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
      writes[i].dstSet = descriptors;
      writes[i].dstBinding = i;
      writes[i].descriptorCount = 1;
      writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
      writes[i].pBufferInfo = &buffers[i];
    }
    vkUpdateDescriptorSets(device, 3, writes, 0, nullptr);
    VkCommandPoolCreateInfo cpinfo{VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO};
    cpinfo.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    cpinfo.queueFamilyIndex = family;
    vkcheck(vkCreateCommandPool(device, &cpinfo, nullptr, &command_pool));
    VkCommandBufferAllocateInfo cbinfo{
        VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO};
    cbinfo.commandPool = command_pool;
    cbinfo.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbinfo.commandBufferCount = 1;
    vkcheck(vkAllocateCommandBuffers(device, &cbinfo, &command));
    VkFenceCreateInfo finfo{VK_STRUCTURE_TYPE_FENCE_CREATE_INFO};
    vkcheck(vkCreateFence(device, &finfo, nullptr, &fence));
    if (timestamp_bits) {
      VkQueryPoolCreateInfo query{VK_STRUCTURE_TYPE_QUERY_POOL_CREATE_INFO};
      query.queryType = VK_QUERY_TYPE_TIMESTAMP;
      query.queryCount = 2;
      vkcheck(vkCreateQueryPool(device, &query, nullptr, &queries));
    }
  }
};
VulkanMiner::VulkanMiner(std::optional<std::uint32_t> device,
                         std::uint32_t group, std::string shader,
                         bool diagnostics)
    : impl_(std::make_unique<Impl>()) {
  impl_->init(device, group, shader, diagnostics);
}
VulkanMiner::~VulkanMiner() = default;
std::string VulkanMiner::pipeline_statistics() const {
  auto &v = *impl_;
  if (!v.diagnostics)
    return "{\"available\":false}";
  auto props = reinterpret_cast<PFN_vkGetPipelineExecutablePropertiesKHR>(
      vkGetDeviceProcAddr(v.device, "vkGetPipelineExecutablePropertiesKHR"));
  auto stats = reinterpret_cast<PFN_vkGetPipelineExecutableStatisticsKHR>(
      vkGetDeviceProcAddr(v.device, "vkGetPipelineExecutableStatisticsKHR"));
  need(props && stats, "Pipeline diagnostics entry points missing");
  auto quote = [](const char *text) {
    std::string out = "\"";
    for (; *text; text++) {
      if (*text == '\\' || *text == '\"')
        out += '\\';
      if (static_cast<unsigned char>(*text) >= 32)
        out += *text;
    }
    return out + '\"';
  };
  VkPipelineInfoKHR info{VK_STRUCTURE_TYPE_PIPELINE_INFO_KHR};
  info.pipeline = v.pipeline;
  std::uint32_t count = 0;
  vkcheck(props(v.device, &info, &count, nullptr));
  need(count <= 32, "Too many pipeline executables");
  std::vector<VkPipelineExecutablePropertiesKHR> executables(count);
  for (auto &e : executables)
    e.sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_PROPERTIES_KHR;
  vkcheck(props(v.device, &info, &count, executables.data()));
  std::ostringstream out;
  out << "{\"available\":true,\"executables\":[";
  for (std::uint32_t i = 0; i < count; i++) {
    if (i)
      out << ',';
    out << "{\"name\":" << quote(executables[i].name)
        << ",\"subgroup_size\":" << executables[i].subgroupSize
        << ",\"statistics\":[";
    VkPipelineExecutableInfoKHR query{
        VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_INFO_KHR};
    query.pipeline = v.pipeline;
    query.executableIndex = i;
    std::uint32_t n = 0;
    vkcheck(stats(v.device, &query, &n, nullptr));
    need(n <= 1024, "Too many pipeline statistics");
    std::vector<VkPipelineExecutableStatisticKHR> values(n);
    for (auto &stat : values) {
      stat.sType = VK_STRUCTURE_TYPE_PIPELINE_EXECUTABLE_STATISTIC_KHR;
      stat.value.u64 = 0;
    }
    vkcheck(stats(v.device, &query, &n, values.data()));
    for (std::uint32_t j = 0; j < n; j++) {
      const auto &stat = values[j];
      if (j)
        out << ',';
      out << "{\"name\":" << quote(stat.name)
          << ",\"description\":" << quote(stat.description) << ",\"value\":";
      switch (stat.format) {
      case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_BOOL32_KHR:
        out << (stat.value.b32 ? "true" : "false");
        break;
      case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_INT64_KHR:
        out << stat.value.i64;
        break;
      case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR:
        out << stat.value.u64;
        break;
      case VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_FLOAT64_KHR:
        out << stat.value.f64;
        break;
      default:
        throw std::runtime_error("Unknown statistic format");
      }
      if (std::strcmp(stat.name, "Local Memory Size") == 0 &&
          stat.format == VK_PIPELINE_EXECUTABLE_STATISTIC_FORMAT_UINT64_KHR &&
          stat.value.u64 > 1073741824ULL)
        out << ",\"interpretation\":\"implausible_per_thread_size_not_used_for_"
               "occupancy\"";
      out << '}';
    }
    out << "]}";
  }
  out << "]}";
  return out.str();
}
std::uint64_t VulkanMiner::command_recordings() const {
  return impl_->recordings;
}
std::string VulkanMiner::device_name() const {
  return impl_->properties.deviceName;
}
std::uint32_t VulkanMiner::device_index() const { return impl_->index; }
std::uint32_t VulkanMiner::workgroup_size() const { return impl_->group; }
void VulkanMiner::prepare(std::span<const unsigned char> header, bool gpu) {
  constexpr char label[] = "pqcoin/pow-block/v1";
  need(header.size() == 268 &&
           std::equal(std::begin(label), std::end(label), header.begin()),
       "Expected canonical 268-byte QCash header");
  Job job{};
  for (unsigned i = 0; i < 136; i++)
    job.midstate[i / 8] ^= std::uint64_t(header[i]) << ((i % 8) * 8);
  if (!gpu)
    keccak(job.midstate);
  impl_->pending_prepare = gpu;
  for (unsigned i = 0; i < 132; i++)
    job.tail[i / 8] |= std::uint64_t(header[i + 136]) << ((i % 8) * 8);
  job.tail[16] |= std::uint64_t(0x06) << 32;
  job.tail[16] |= std::uint64_t(0x80) << 56;
  for (unsigned i = 0; i < 32; i++)
    job.target[i / 4] |= std::uint32_t(header[100 + i]) << ((3 - i % 4) * 8);
  std::memcpy(impl_->input.mapped, &job, sizeof(job));
  impl_->cache(impl_->input, true);
  impl_->prepared = true;
}
VulkanBatch VulkanMiner::run(std::uint64_t start, std::uint32_t count,
                             bool capture, std::uint32_t dispatch_size) {
  auto &v = *impl_;
  need(v.prepared, "No prepared work template");
  need(dispatch_size > 0 && dispatch_size <= max_dispatch,
       "Invalid dispatch size");
  need(count > 0 && count <= max_batch && (!capture || count <= 4096),
       "Invalid Vulkan batch/capture count");
  need(start <= std::numeric_limits<std::uint64_t>::max() - (count - 1),
       "Nonce window overflows");
  *static_cast<std::uint32_t *>(v.output.mapped) =
      std::numeric_limits<std::uint32_t>::max();
  v.cache(v.output, true);
  std::uint32_t params[] = {static_cast<std::uint32_t>(start),
                            static_cast<std::uint32_t>(start >> 32), count,
                            capture ? 1U : 0U};
  std::memcpy(v.parameters.mapped, params, sizeof(params));
  v.cache(v.parameters, true);
  if (v.cached_count != count || v.cached_dispatch != dispatch_size ||
      v.cached_prepare != v.pending_prepare) {
    vkcheck(vkResetCommandBuffer(v.command, 0));
    VkCommandBufferBeginInfo begin{VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO};
    vkcheck(vkBeginCommandBuffer(v.command, &begin));
    VkMemoryBarrier before{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    before.srcAccessMask = VK_ACCESS_HOST_WRITE_BIT;
    before.dstAccessMask =
        VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    vkCmdPipelineBarrier(v.command, VK_PIPELINE_STAGE_HOST_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &before, 0,
                         nullptr, 0, nullptr);
    if (v.queries) {
      vkCmdResetQueryPool(v.command, v.queries, 0, 2);
      vkCmdWriteTimestamp(v.command, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT,
                          v.queries, 0);
    }
    vkCmdBindDescriptorSets(v.command, VK_PIPELINE_BIND_POINT_COMPUTE,
                            v.pipeline_layout, 0, 1, &v.descriptors, 0,
                            nullptr);
    VkMemoryBarrier between{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    between.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    between.dstAccessMask =
        VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    if (v.pending_prepare) {
      vkCmdBindPipeline(v.command, VK_PIPELINE_BIND_POINT_COMPUTE,
                        v.prepare_pipeline);
      vkCmdDispatch(v.command, 1, 1, 1);
      vkCmdPipelineBarrier(v.command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                           VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &between,
                           0, nullptr, 0, nullptr);
    }
    vkCmdBindPipeline(v.command, VK_PIPELINE_BIND_POINT_COMPUTE, v.pipeline);
    for (std::uint32_t base = 0; base < count;) {
      auto n = std::min(dispatch_size, count - base);
      std::uint32_t offset[] = {base, n};
      vkCmdPushConstants(v.command, v.pipeline_layout,
                         VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(offset),
                         offset);
      vkCmdDispatch(v.command, (n + v.group - 1) / v.group, 1, 1);
      base += n;
      if (base < count)
        vkCmdPipelineBarrier(v.command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                             VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1,
                             &between, 0, nullptr, 0, nullptr);
    }
    if (v.queries)
      vkCmdWriteTimestamp(v.command, VK_PIPELINE_STAGE_BOTTOM_OF_PIPE_BIT,
                          v.queries, 1);
    VkMemoryBarrier after{VK_STRUCTURE_TYPE_MEMORY_BARRIER};
    after.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    after.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    vkCmdPipelineBarrier(v.command, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         VK_PIPELINE_STAGE_HOST_BIT, 0, 1, &after, 0, nullptr,
                         0, nullptr);
    vkcheck(vkEndCommandBuffer(v.command));
    ++v.recordings;
    v.cached_count = count;
    v.cached_dispatch = dispatch_size;
    v.cached_prepare = v.pending_prepare;
  }
  vkcheck(vkResetFences(v.device, 1, &v.fence));
  VkSubmitInfo submit{VK_STRUCTURE_TYPE_SUBMIT_INFO};
  submit.commandBufferCount = 1;
  submit.pCommandBuffers = &v.command;
  vkcheck(vkQueueSubmit(v.queue, 1, &submit, v.fence));
  vkcheck(vkWaitForFences(v.device, 1, &v.fence, VK_TRUE, 30000000000ULL));
  v.cache(v.output, false);
  v.pending_prepare = false;
  VulkanBatch result;
  auto *words = static_cast<std::uint32_t *>(v.output.mapped);
  if (words[0] != std::numeric_limits<std::uint32_t>::max()) {
    need(words[0] < count, "GPU returned invalid index");
    result.winner = words[0];
  }
  if (v.queries) {
    std::uint64_t ticks[2]{};
    vkcheck(vkGetQueryPoolResults(
        v.device, v.queries, 0, 2, sizeof(ticks), ticks, sizeof(ticks[0]),
        VK_QUERY_RESULT_64_BIT | VK_QUERY_RESULT_WAIT_BIT));
    auto mask = v.timestamp_bits == 64
                    ? ~std::uint64_t(0)
                    : (std::uint64_t(1) << v.timestamp_bits) - 1;
    result.gpu_seconds = double((ticks[1] - ticks[0]) & mask) *
                         double(v.properties.limits.timestampPeriod) * 1e-9;
  }
  if (capture) {
    result.digests.resize(count);
    for (std::uint32_t i = 0; i < count; i++)
      for (unsigned j = 0; j < 32; j++)
        result.digests[i][j] = static_cast<unsigned char>(
            words[4 + i * 8 + j / 4] >> ((3 - j % 4) * 8));
  }
  return result;
}
} // namespace pqcoin
